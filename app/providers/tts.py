import hashlib
import io
import json
import math
import struct
import time
import wave

import httpx

from app.config import Settings
from app.providers.errors import ProviderError
from app.providers.voices import PRESET_VOICES, Voice


class SynthRequest:
    def __init__(self, speaker_id: str, speaker_name: str, voice_id: str, text: str):
        self.speaker_id = speaker_id
        self.speaker_name = speaker_name
        self.voice_id = voice_id
        self.text = text


def speaker_key(speaker_id: str) -> str:
    digest = hashlib.sha1(speaker_id.encode("utf-8")).hexdigest()[:10]
    return f"spk_{digest}"


def write_tone(path, duration: float, frequency: float, framerate: int = 22050) -> None:
    count = max(1, int(duration * framerate))
    fade = max(1, int(0.02 * framerate))
    amplitude = 0.22
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(framerate)
        frames = bytearray()
        for index in range(count):
            envelope = 1.0
            if index < fade:
                envelope = index / fade
            elif index > count - fade:
                envelope = max(0.0, (count - index) / fade)
            sample = int(amplitude * envelope * 32767 * math.sin(2 * math.pi * frequency * index / framerate))
            frames += struct.pack("<h", max(-32767, min(32767, sample)))
        handle.writeframes(frames)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buffer.getvalue())


def _tone_frequency(name: str) -> float:
    ladder = (196.0, 220.0, 247.0, 294.0, 330.0, 349.0, 392.0, 440.0, 494.0)
    digest = hashlib.sha256(name.encode("utf-8")).digest()[0]
    return ladder[digest % len(ladder)]


class MockTTS:
    name = "mock"

    def __init__(self, settings: Settings):
        self.settings = settings

    def close(self) -> None:
        return None

    def list_voices(self) -> list[Voice]:
        return list(PRESET_VOICES)

    def synthesize_line(self, request: SynthRequest, dest) -> None:
        per_char = self.settings.mock_tts_sec_per_char
        duration = len(request.text.strip()) * per_char
        duration = max(self.settings.mock_tts_min_sec, min(self.settings.mock_tts_max_sec, duration))
        write_tone(dest, duration, _tone_frequency(request.speaker_name or request.speaker_id))

    def synthesize_dialogue(self, requests: list[SynthRequest], dest) -> list[tuple[float, float]] | None:
        if not requests:
            raise ProviderError("没有台词")
        pieces = []
        timings: list[tuple[float, float]] = []
        cursor = 0.0
        dest.parent.mkdir(parents=True, exist_ok=True)
        for index, request in enumerate(requests):
            piece = dest.with_name(f"{dest.stem}-{index}.wav")
            self.synthesize_line(request, piece)
            duration = _wav_duration(piece)
            timings.append((cursor, cursor + duration))
            pieces.append(piece)
            cursor += duration
        _concat_wavs(pieces, dest)
        for piece in pieces:
            piece.unlink(missing_ok=True)
        return timings


class RetryableTTS(ProviderError):
    def __init__(self, message: str, wait: float | None = None):
        super().__init__(message)
        self.wait = wait


class MosiTTSProvider:
    """MOSI 多人语音。

    按对话场景调用 POST /v1/audio/speech/speakers（模型 moss-ttsd-1.0）。
    每一句单独作为一段 segments 提交，从而得到可对齐字幕的时长，再由流水线拼接成一集。
    这样角色超过两位时也不会超出单次请求的说话人组合。
    429 和 5xx 会按 Retry-After 或 MOSS_RETRY_WAIT_SEC 重试。
    文档：https://platform.mosi.cn/docs/scenarios/dialogue
    """

    name = "mosi"

    def __init__(self, settings: Settings, client: httpx.Client | None = None, sleep=time.sleep):
        self.settings = settings
        self._sleep = sleep
        self._client = client or httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0), follow_redirects=True)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _base(self) -> str:
        return self.settings.moss_base_url.rstrip("/")

    def _headers(self, json_body: bool = True) -> dict[str, str]:
        key = self.settings.resolved_mosi_key()
        if not key:
            raise ProviderError("未配置 MOSI_API_KEY")
        headers = {
            "Authorization": f"Bearer {key}",
            "Platform": "moss-platform",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        attempts = max(1, int(self.settings.moss_max_attempts))
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TimeoutException as exc:
                last = RetryableTTS("MOSI 请求超时")
                self._pause(attempt, attempts, last)
                continue
            except httpx.TransportError as exc:
                last = RetryableTTS(f"MOSI 请求失败：{exc}")
                self._pause(attempt, attempts, last)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last = RetryableTTS(
                    f"MOSI 请求失败（{response.status_code}）：{response.text[:240]}",
                    wait=_retry_after(response),
                )
                self._pause(attempt, attempts, last)
                continue
            return response
        raise ProviderError(f"MOSI 请求失败，已试 {attempts} 次：{last}") from last

    def _pause(self, attempt: int, attempts: int, error: RetryableTTS) -> None:
        if attempt >= attempts:
            return
        wait = self.settings.moss_retry_wait_sec if error.wait is None else error.wait
        wait = max(0.0, min(60.0, float(wait)))
        if wait:
            self._sleep(wait)

    def list_voices(self) -> list[Voice]:
        voices: list[Voice] = []
        after: str | None = None
        for _ in range(6):
            params: dict[str, str | int] = {"limit": 100}
            if after:
                params["after"] = after
            response = self._request(
                "GET",
                f"{self._base()}/v1/audio/voices",
                headers=self._headers(False),
                params=params,
            )
            self._raise(response)
            payload = response.json()
            for item in payload.get("data") or []:
                preview = item.get("preview_url") or item.get("sample_url") or item.get("demo_url") or ""
                voices.append(
                    Voice(
                        id=str(item.get("id") or ""),
                        name=str(item.get("name") or item.get("id") or "未命名音色"),
                        description=str(item.get("description") or ""),
                        language=str(item.get("language") or ""),
                        preview_url=str(preview or ""),
                    )
                )
            if not payload.get("has_more"):
                break
            after = payload.get("next_cursor")
            if not after:
                break
        return [voice for voice in voices if voice.id]

    def synthesize_line(self, request: SynthRequest, dest) -> None:
        text = request.text.strip()
        if not text:
            raise ProviderError("台词是空的")
        if not request.voice_id:
            raise ProviderError(f"角色「{request.speaker_name or '未知'}」还没有音色")
        key = speaker_key(request.speaker_id or request.speaker_name or "speaker")
        payload: dict = {
            "model": self.settings.moss_model,
            "speakers": [{"id": key, "voice_id": request.voice_id}],
            "segments": [{"speaker": key, "text": text}],
            "response_format": self.settings.moss_response_format,
            "delivery_method": self.settings.moss_delivery_method,
        }
        if self._use_async(text):
            payload["async"] = True
        response = self._request(
            "POST",
            f"{self._base()}/v1/audio/speech/speakers",
            headers=self._headers(),
            json=payload,
        )
        audio, _meta = self._audio_from_response(response)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(audio)

    def synthesize_dialogue(self, requests: list[SynthRequest], dest) -> list[tuple[float, float]] | None:
        usable = [request for request in requests if request.text.strip()]
        if not usable:
            raise ProviderError("台词是空的")
        speakers: list[dict[str, str]] = []
        seen: set[str] = set()
        segments = []
        for request in usable:
            if not request.voice_id:
                raise ProviderError(f"角色「{request.speaker_name or '未知'}」还没有音色")
            key = speaker_key(request.speaker_id or request.speaker_name or "speaker")
            if key not in seen:
                if len(seen) >= 2:
                    raise ProviderError("一段对话最多两位说话人")
                seen.add(key)
                speakers.append({"id": key, "voice_id": request.voice_id})
            segments.append({"speaker": key, "text": request.text.strip()})
        payload: dict = {
            "model": self.settings.moss_model,
            "speakers": speakers,
            "segments": segments,
            "response_format": self.settings.moss_response_format,
            "delivery_method": self.settings.moss_delivery_method,
        }
        combined = "".join(item["text"] for item in segments)
        if self._use_async(combined):
            payload["async"] = True
        response = self._request(
            "POST",
            f"{self._base()}/v1/audio/speech/speakers",
            headers=self._headers(),
            json=payload,
        )
        audio, meta = self._audio_from_response(response)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(audio)
        return _timings_from_meta(meta, len(usable))

    def _use_async(self, text: str) -> bool:
        mode = (self.settings.moss_async or "auto").strip().lower()
        if mode == "always":
            return True
        if mode == "never":
            return False
        return len(text) >= 280

    def _raise(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        detail = response.text[:500]
        try:
            body = response.json()
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                detail = error.get("message") or error.get("code") or detail
            elif isinstance(error, str):
                detail = error
        except (json.JSONDecodeError, ValueError):
            pass
        raise ProviderError(f"MOSI 请求失败（{response.status_code}）：{detail}")

    def _audio_from_response(self, response: httpx.Response) -> tuple[bytes, dict]:
        self._raise(response)
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type.startswith("audio/") or content_type == "application/octet-stream":
            if not response.content:
                raise ProviderError("MOSI 返回了空音频")
            return response.content, {}
        try:
            data = response.json()
        except json.JSONDecodeError:
            if response.content:
                return response.content, {}
            raise ProviderError("MOSI 响应无法解析")
        if not isinstance(data, dict):
            raise ProviderError("MOSI 响应格式不正确")
        status = str(data.get("status") or "").upper()
        if status == "FAILED":
            raise ProviderError(f"MOSI 合成失败：{data.get('error')}")
        url = data.get("url")
        if url and status in {"", "SUCCESS", "SUCCEEDED", "COMPLETED"}:
            return self._download(str(url)), data
        task_id = data.get("task_id") or data.get("id")
        if task_id and status in {"", "PENDING", "PROCESSING", "QUEUED"}:
            return self._poll(str(task_id))
        if url:
            return self._download(str(url)), data
        raise ProviderError(f"无法解析 MOSI 响应：{str(data)[:300]}")

    def _download(self, url: str) -> bytes:
        # 结果经常在对象存储的签名地址上。把 MOSI 的 Authorization 带过去会被拒绝。
        headers = self._headers(False) if _same_host(url, self._base()) else {}
        response = self._request("GET", url, headers=headers)
        self._raise(response)
        if not response.content:
            raise ProviderError("MOSI 音频地址没有内容")
        return response.content

    def _poll(self, task_id: str) -> tuple[bytes, dict]:
        for _ in range(90):
            response = self._request(
                "GET",
                f"{self._base()}/v1/audio/tasks/{task_id}",
                headers=self._headers(False),
            )
            self._raise(response)
            data = response.json()
            status = str(data.get("status") or "").upper()
            if status == "SUCCESS":
                url = data.get("url")
                if not url:
                    raise ProviderError("MOSI 任务成功但没有音频地址")
                return self._download(str(url)), data
            if status == "FAILED":
                raise ProviderError(f"MOSI 任务失败：{data.get('error')}")
            delay = 2.0
            retry_after = data.get("retry_after")
            try:
                if retry_after is not None:
                    delay = min(10.0, max(0.2, float(retry_after)))
            except (TypeError, ValueError):
                delay = 2.0
            self._sleep(delay)
        raise ProviderError("MOSI 任务超时")


def _same_host(url: str, base: str) -> bool:
    try:
        return (httpx.URL(url).host or "").lower() == (httpx.URL(base).host or "").lower()
    except Exception:
        return False


def _timings_from_meta(meta: dict, count: int) -> list[tuple[float, float]] | None:
    for key in ("segments", "sentences", "subtitles", "timestamps"):
        items = meta.get(key)
        if not isinstance(items, list):
            continue
        spans: list[tuple[float, float]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            start = _num(item.get("start") or item.get("start_time") or item.get("begin_time") or item.get("begin"))
            end = _num(item.get("end") or item.get("end_time") or item.get("stop"))
            if start is None:
                continue
            if end is None:
                duration = _num(item.get("duration") or item.get("duration_sec"))
                if duration is None:
                    continue
                end = start + duration
            if end > start:
                spans.append((float(start), float(end)))
        if len(spans) == count:
            return spans
    return None


def _num(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _wav_duration(path) -> float:
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate() or 1
        return frames / rate


def _concat_wavs(paths, dest) -> None:
    with wave.open(str(paths[0]), "rb") as first:
        params = first.getparams()
        frames = first.readframes(first.getnframes())
    for path in paths[1:]:
        with wave.open(str(path), "rb") as handle:
            frames += handle.readframes(handle.getnframes())
    with wave.open(str(dest), "wb") as output:
        output.setparams(params)
        output.writeframes(frames)


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None

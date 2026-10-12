import base64
import hashlib
import logging
import time

import httpx
from PIL import Image, ImageDraw, ImageFont

from app.config import Settings
from app.fonts import find_cjk_font
from app.providers.errors import ProviderError

logger = logging.getLogger(__name__)


class MockImageProvider:
    name = "mock"

    def close(self) -> None:
        return None

    def generate(self, prompt: str, dest, width: int, height: int) -> str | None:
        width = max(320, width - (width % 2))
        height = max(180, height - (height % 2))
        digest = hashlib.sha256(prompt.encode("utf-8")).digest()
        palettes = (
            ((244, 232, 214), (143, 47, 42), (35, 66, 55)),
            ((232, 224, 208), (36, 58, 92), (166, 124, 66)),
            ((236, 226, 216), (90, 52, 42), (70, 92, 78)),
            ((228, 220, 206), (120, 62, 48), (48, 72, 78)),
        )
        paper, ink, accent = palettes[digest[0] % len(palettes)]
        image = Image.new("RGB", (width, height), paper)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, width, int(height * 0.08)), fill=ink)
        draw.rectangle((int(width * 0.08), int(height * 0.18), int(width * 0.62), int(height * 0.86)), outline=ink, width=4)
        draw.ellipse(
            (int(width * 0.58), int(height * 0.22), int(width * 0.92), int(height * 0.78)),
            fill=accent,
        )
        draw.polygon(
            [
                (int(width * 0.16), int(height * 0.72)),
                (int(width * 0.34), int(height * 0.40)),
                (int(width * 0.52), int(height * 0.72)),
            ],
            fill=ink,
        )
        font = _load_font(max(18, height // 18))
        small = _load_font(max(14, height // 28))
        title = _first_sentence(prompt)[:28] or "场景"
        draw.text((int(width * 0.06), int(height * 0.015)), "章映", fill=paper, font=small)
        y = int(height * 0.22)
        for line in _wrap(title, 12)[:4]:
            draw.text((int(width * 0.12), y), line, fill=ink, font=font)
            y += int(height * 0.08)
        dest.parent.mkdir(parents=True, exist_ok=True)
        image.save(dest, format="PNG")
        return None


class ImageRequestError(ProviderError):
    """可以再试一次的图像失败。"""


class OpenAIImageProvider:
    """OpenAI 兼容的 images/generations，默认打到图像中转。"""

    name = "openai"

    def __init__(self, settings: Settings, client: httpx.Client | None = None, sleep=time.sleep):
        self.settings = settings
        self._sleep = sleep
        timeout = httpx.Timeout(settings.image_timeout_sec, connect=30.0)
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def generate(self, prompt: str, dest, width: int, height: int) -> str | None:
        del width, height
        attempts = max(1, int(self.settings.image_max_attempts))
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self._once(prompt, dest)
            except ImageRequestError as exc:
                last = exc
                if attempt >= attempts:
                    break
                wait = self.settings.image_retry_wait_sec
                logger.warning("图像第 %s/%s 次失败，%.0f 秒后重试：%s", attempt, attempts, wait, exc)
                if wait > 0:
                    self._sleep(wait)
        raise ProviderError(f"图像生成失败，已试 {attempts} 次：{last}") from last

    def _once(self, prompt: str, dest) -> str | None:
        key = self.settings.image_api_key.strip()
        if not key:
            raise ProviderError("未配置 IMAGE_API_KEY")
        try:
            size = self.settings.resolved_image_size()
        except ValueError as exc:
            raise ProviderError(str(exc)) from exc
        url = self.settings.resolved_image_base_url() + "/images/generations"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        payload = {
            "model": self.settings.resolved_image_model(),
            "prompt": prompt,
            "size": size,
            "response_format": "b64_json",
        }
        response = self._post(url, headers, payload)
        if response.status_code == 400 and "response_format" in payload:
            payload.pop("response_format")
            response = self._post(url, headers, payload)
        if response.status_code == 429 or response.status_code >= 500:
            raise ImageRequestError(f"图像请求失败（{response.status_code}）：{response.text[:300]}")
        if response.status_code >= 400:
            raise ProviderError(f"图像请求失败（{response.status_code}）：{response.text[:300]}")
        try:
            item = response.json()["data"][0]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ImageRequestError("图像接口没有返回图片") from exc
        source = item.get("url") or None
        if item.get("b64_json"):
            try:
                raw = base64.b64decode(item["b64_json"])
            except (ValueError, TypeError) as exc:
                raise ImageRequestError("图像数据无法解码") from exc
        elif source:
            raw = self._download(source)
        else:
            raise ImageRequestError("图像接口没有返回图片")
        if not raw:
            raise ImageRequestError("图像内容是空的")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        return source

    def _post(self, url: str, headers: dict, payload: dict) -> httpx.Response:
        try:
            return self._client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise ImageRequestError("图像请求超时") from exc
        except httpx.TransportError as exc:
            raise ImageRequestError(f"图像请求失败：{exc}") from exc

    def _download(self, url: str) -> bytes:
        try:
            downloaded = self._client.get(url)
        except httpx.TimeoutException as exc:
            raise ImageRequestError("图像地址下载超时") from exc
        except httpx.TransportError as exc:
            raise ImageRequestError(f"图像地址无法下载：{exc}") from exc
        if downloaded.status_code >= 500 or downloaded.status_code == 429:
            raise ImageRequestError(f"图像地址无法下载（{downloaded.status_code}）")
        if downloaded.status_code >= 400 or not downloaded.content:
            raise ProviderError("图像地址无法下载")
        return downloaded.content


def _load_font(size: int):
    info = find_cjk_font()
    if info is None:
        return ImageFont.load_default()
    try:
        return ImageFont.truetype(str(info.file), size=size)
    except OSError:
        return ImageFont.load_default()


def _wrap(text: str, limit: int) -> list[str]:
    lines: list[str] = []
    buf = ""
    for char in text:
        buf += char
        if len(buf) >= limit:
            lines.append(buf)
            buf = ""
    if buf:
        lines.append(buf)
    return lines or [""]


def _first_sentence(prompt: str) -> str:
    for marker in ("画面：", "画面:"):
        if marker in prompt:
            prompt = prompt.split(marker, 1)[1]
            break
    prompt = prompt.replace("\n", "")
    for end in ("。", "！", "？"):
        if end in prompt:
            return prompt.split(end, 1)[0]
    return prompt[:40]

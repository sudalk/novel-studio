import json

import httpx
import pytest

from app.config import Settings
from app.providers.errors import ProviderError
from app.providers.tts import MosiTTSProvider, SynthRequest, write_tone


def _settings(**overrides):
    base = dict(
        app_mode="live",
        tts_provider="mosi",
        mosi_api_key="",
        moss_api_key="test-key",
        image_api_key="",
        openai_api_key="",
        deepseek_api_key="",
        moss_base_url="https://api.mosi.cn",
        moss_model="moss-ttsd-1.0",
        moss_response_format="wav",
        moss_delivery_method="url",
        moss_async="never",
        data_dir="/tmp",
    )
    base.update(overrides)
    return Settings(**base)


def _wav_bytes(tmp_path):
    path = tmp_path / "tone.wav"
    write_tone(path, 0.2, 440)
    return path.read_bytes()


def test_dialogue_request_matches_mosi_docs(tmp_path):
    captured = {}
    wav = _wav_bytes(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/audio/speech/speakers":
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content.decode())
            return httpx.Response(200, json={"id": "job1", "status": "SUCCESS", "url": "https://cdn.example/out.wav"})
        if request.url.host == "cdn.example":
            captured["cdn_authorization"] = request.headers.get("authorization")
            return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})
        raise AssertionError(request.url)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MosiTTSProvider(_settings(), client=client)
    dest = tmp_path / "line.wav"
    provider.synthesize_line(SynthRequest("char-lin", "林晚", "voice-lin", "不要再往北走了。"), dest)
    provider.close()

    assert captured["headers"]["authorization"] == "Bearer test-key"
    assert captured["headers"]["platform"] == "moss-platform"
    body = captured["body"]
    assert body["model"] == "moss-ttsd-1.0"
    assert body["delivery_method"] == "url"
    assert body["response_format"] == "wav"
    speaker = body["speakers"][0]
    assert speaker["voice_id"] == "voice-lin"
    assert body["segments"] == [{"speaker": speaker["id"], "text": "不要再往北走了。"}]
    assert dest.read_bytes().startswith(b"RIFF")
    assert captured["cdn_authorization"] is None


def test_async_task_is_polled(tmp_path):
    wav = _wav_bytes(tmp_path)
    calls = {"task": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/audio/speech/speakers":
            return httpx.Response(200, json={"task_id": "task-9", "status": "PENDING"})
        if request.url.path == "/v1/audio/tasks/task-9":
            calls["task"] += 1
            if calls["task"] == 1:
                return httpx.Response(200, json={"id": "task-9", "status": "PROCESSING", "retry_after": 0.01})
            return httpx.Response(
                200,
                json={"id": "task-9", "status": "SUCCESS", "url": "https://cdn.example/out.wav", "object": "audio.speech"},
            )
        if request.url.host == "cdn.example":
            return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})
        raise AssertionError(str(request.url))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MosiTTSProvider(_settings(moss_async="always"), client=client)
    dest = tmp_path / "async.wav"
    provider.synthesize_line(SynthRequest("narrator", "旁白", "voice-narr", "雨下了一夜。"), dest)
    assert calls["task"] == 2
    assert dest.exists()


def test_two_speakers_share_one_request(tmp_path):
    captured = {}
    wav = _wav_bytes(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MosiTTSProvider(_settings(), client=client)
    dest = tmp_path / "pair.wav"
    provider.synthesize_dialogue(
        [
            SynthRequest("narrator", "旁白", "voice-narr", "雨下了一夜。"),
            SynthRequest("lin", "林晚", "voice-lin", "别往北走。"),
        ],
        dest,
    )
    provider.close()
    body = captured["body"]
    assert body["model"] == "moss-ttsd-1.0"
    assert [item["voice_id"] for item in body["speakers"]] == ["voice-narr", "voice-lin"]
    assert [item["text"] for item in body["segments"]] == ["雨下了一夜。", "别往北走。"]
    assert body["segments"][0]["speaker"] == body["speakers"][0]["id"]
    assert dest.read_bytes().startswith(b"RIFF")


def test_missing_key(tmp_path):
    provider = MosiTTSProvider(_settings(moss_api_key="", mosi_api_key=""))
    try:
        with pytest.raises(ProviderError, match="MOSI_API_KEY"):
            provider.synthesize_line(SynthRequest("a", "甲", "voice", "你好"), tmp_path / "ignored.wav")
    finally:
        provider.close()


def test_mosi_api_key_is_accepted(tmp_path):
    wav = _wav_bytes(tmp_path)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MosiTTSProvider(_settings(moss_api_key="", mosi_api_key="from-mosi"), client=client)
    provider.synthesize_line(SynthRequest("a", "甲", "v", "你好"), tmp_path / "alias.wav")
    provider.close()
    assert captured["authorization"] == "Bearer from-mosi"


def test_rate_limit_is_retried(tmp_path):
    wav = _wav_bytes(tmp_path)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/speakers"):
            raise AssertionError(str(request.url))
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": {"message": "slow down"}})
        return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MosiTTSProvider(_settings(), client=client, sleep=lambda _seconds: None)
    dest = tmp_path / "retry.wav"
    provider.synthesize_line(SynthRequest("a", "甲", "v", "再试一次"), dest)
    provider.close()
    assert calls["n"] == 2
    assert dest.read_bytes() == wav


def test_binary_audio_response(tmp_path):
    wav = _wav_bytes(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=wav, headers={"content-type": "audio/wav"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MosiTTSProvider(_settings(moss_delivery_method="audio"), client=client)
    dest = tmp_path / "bin.wav"
    provider.synthesize_line(SynthRequest("a", "甲", "v", "一句台词"), dest)
    assert dest.read_bytes() == wav

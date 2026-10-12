import base64
import json
from pathlib import Path

import httpx
from PIL import Image

from app.config import Settings
from app.providers.image import OpenAIImageProvider
from app.services.scenes import (
    bind_visuals,
    missing_cache_keys,
    plan_scenes,
    stamp_cache_keys,
)
from app.services.video import coalesce_holds


def _settings(tmp_path, **overrides):
    data = {
        "app_mode": "mock",
        "llm_provider": "mock",
        "tts_provider": "mock",
        "image_provider": "openai",
        "image_api_key": "test-image-key",
        "image_models": "",
        "image_model": "",
        "image_timeout_sec": 120,
        "image_max_per_episode": 0,
        "data_dir": str(tmp_path),
    }
    data.update(overrides)
    return Settings(**data)


def _png_bytes() -> bytes:
    import io

    buffer = io.BytesIO()
    image = Image.new("RGB", (8, 12), (20, 30, 40))
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _Stub:
    def __init__(self, scenes):
        self.scenes = scenes

    def complete_json(self, system, user):
        del system, user
        return {"scenes": self.scenes}


def _four_lines():
    return [{"speaker": "旁白", "text": str(index)} for index in range(4)]


def _storyboard():
    return [
        {"start_line": 0, "end_line": 0, "summary": "铺垫", "prompt": "", "key": False},
        {"start_line": 1, "end_line": 1, "summary": "揭示", "prompt": "信落在窗台", "key": True},
        {"start_line": 2, "end_line": 2, "summary": "对话", "prompt": "", "key": False},
        {"start_line": 3, "end_line": 3, "summary": "烧信", "prompt": "火光映在脸上", "key": True},
    ]


def test_default_models_and_no_cap(tmp_path):
    settings = _settings(tmp_path)
    assert settings.resolved_image_models() == [
        "gpt-image-2-1k",
        "gemini-nano-banana-2.1",
        "gpt-image-2.5-flare",
    ]
    assert settings.resolved_image_model() == "gpt-image-2-1k"
    assert settings.image_timeout_sec == 120
    assert settings.resolved_image_cap() is None
    listed = _settings(tmp_path, image_models="a， b, c")
    assert listed.resolved_image_models() == ["a", "b", "c"]
    single = _settings(tmp_path, image_model="only-one")
    assert single.resolved_image_models() == ["only-one"]


def test_ordinary_scenes_reuse_the_latest_key():
    bound = bind_visuals(_storyboard(), None)
    assert [scene["key"] for scene in bound] == [False, True, False, True]
    assert [scene["visual_of"] for scene in bound] == [1, 1, 1, 3]
    scenes = plan_scenes(_four_lines(), [{"name": "旁白", "description": ""}], _Stub(_storyboard()))
    assert sum(scene["key"] for scene in scenes) == 2
    assert scenes[0]["prompt"] == ""
    assert "STYLE" in scenes[1]["prompt"]
    assert scenes[2]["visual_of"] == 1
    stamp_cache_keys(scenes, "1024x1792")
    assert scenes[0]["cache_key"] == scenes[1]["cache_key"]
    assert scenes[2]["cache_key"] == scenes[1]["cache_key"]
    assert scenes[3]["cache_key"] != scenes[1]["cache_key"]
    missing = missing_cache_keys(scenes, set())
    assert missing == [scenes[1]["cache_key"], scenes[3]["cache_key"]]
    assert missing_cache_keys(scenes, {scenes[1]["cache_key"]}) == [scenes[3]["cache_key"]]


def test_optional_cap_keeps_one_key():
    scenes = plan_scenes(
        _four_lines(),
        [{"name": "旁白", "description": ""}],
        _Stub(_storyboard()),
        max_images=1,
    )
    assert sum(scene["key"] for scene in scenes) == 1
    assert scenes[1]["key"] is True
    assert all(scene["visual_of"] == 1 for scene in scenes)


def test_same_image_is_one_continuous_hold():
    images = [(0, 1, Path("a.png")), (2, 4, Path("a.png")), (5, 6, Path("b.png"))]
    merged, lengths = coalesce_holds(images, [1.5, 2.5, 0.5])
    assert merged == [(0, 4, Path("a.png")), (5, 6, Path("b.png"))]
    assert lengths == [4.0, 0.5]


def test_rotation_and_fallback(tmp_path):
    OpenAIImageProvider.reset_rotation()
    png = base64.b64encode(_png_bytes()).decode()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        calls.append(body["model"])
        assert body["size"] == "1024x1792"
        if body["model"] == "gpt-image-2-1k":
            raise httpx.TimeoutException("slow")
        return httpx.Response(200, json={"data": [{"b64_json": png}]})

    settings = _settings(
        tmp_path,
        image_models="gpt-image-2-1k,gemini-nano-banana-2.1,gpt-image-2.5-flare",
        image_aspect="9:16",
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAIImageProvider(settings, client=client)
    first = tmp_path / "first.png"
    provider.generate("雨夜巷口", first, 1080, 1920)
    assert provider.last_model == "gemini-nano-banana-2.1"
    assert calls == ["gpt-image-2-1k", "gemini-nano-banana-2.1"]
    assert first.is_file()
    second = tmp_path / "second.png"
    provider.generate("窗台", second, 1080, 1920)
    assert calls == ["gpt-image-2-1k", "gemini-nano-banana-2.1", "gemini-nano-banana-2.1"]
    assert provider.last_model == "gemini-nano-banana-2.1"
    provider.close()

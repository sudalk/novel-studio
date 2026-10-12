import json

import httpx
import pytest

from app.config import Settings
from app.providers.errors import ProviderError
from app.providers.factory import build_llm
from app.providers.llm import DeepSeekLLM


def test_deepseek_default_model_is_v41_flash(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"characters":[],"lines":[{"speaker":"旁白","text":"雨。"}]}'}}]},
        )

    settings = Settings(
        app_mode="mock",
        llm_provider="deepseek",
        deepseek_api_key="deepseek-test-key",
        deepseek_base_url="https://api.deepseek.com",
        llm_model="",
        data_dir=str(tmp_path),
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = build_llm(settings, client=client)
    assert isinstance(provider, DeepSeekLLM)
    data = provider.complete_json("系统", "正文")
    provider.close()
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == "Bearer deepseek-test-key"
    assert captured["body"]["model"] == "deepseek-flash"
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert data["lines"][0]["text"] == "雨。"
    assert settings.resolved_llm_model() == "deepseek-flash"


def test_deepseek_requires_key(tmp_path):
    settings = Settings(
        llm_provider="deepseek",
        deepseek_api_key="",
        data_dir=str(tmp_path),
    )
    with pytest.raises(ProviderError, match="DEEPSEEK_API_KEY"):
        build_llm(settings)


def test_explicit_llm_model_overrides_default(tmp_path):
    settings = Settings(
        llm_provider="deepseek",
        llm_model="deepseek-v4-pro",
        data_dir=str(tmp_path),
    )
    assert settings.resolved_llm_model() == "deepseek-v4-pro"

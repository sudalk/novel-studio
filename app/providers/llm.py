import json
import re

import httpx

from app.config import Settings
from app.providers.errors import ProviderError
from app.services.heuristics import heuristic_scenes, heuristic_script


def parse_json_loose(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ProviderError("模型没有返回 JSON")
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ProviderError("模型返回的 JSON 无法解析") from exc
    if not isinstance(data, dict):
        raise ProviderError("模型返回的 JSON 不是对象")
    return data


class MockLLM:
    name = "mock"

    def complete_json(self, system: str, user: str) -> dict:
        if "分镜" in system:
            return heuristic_scenes(user)
        return heuristic_script(user)

    def close(self) -> None:
        return None


class OpenAILLM:
    """OpenAI 兼容的 Chat Completions。"""

    name = "openai"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        key = settings.openai_api_key.strip()
        if not key:
            raise ProviderError("未配置 OPENAI_API_KEY")
        self._setup(
            settings,
            key,
            settings.openai_base_url,
            settings.resolved_llm_model() if settings.resolved_llm() == "openai" else (settings.llm_model.strip() or "gpt-4o-mini"),
            client,
            extra=None,
        )

    def _setup(self, settings: Settings, api_key: str, base_url: str, model: str, client, extra: dict | None) -> None:
        self.settings = settings
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._extra = extra or {}
        self._client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0))
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def complete_json(self, system: str, user: str) -> dict:
        url = self._base_url + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        base_payload = {
            "model": self._model,
            "temperature": 0.4,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **self._extra,
        }
        payload = {**base_payload, "response_format": {"type": "json_object"}}
        response = self._client.post(url, headers=headers, json=payload)
        if response.status_code == 400 and "response_format" in payload:
            response = self._client.post(url, headers=headers, json=base_payload)
        if response.status_code >= 400:
            raise ProviderError(f"文本模型请求失败（{response.status_code}）：{response.text[:400]}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("文本模型响应缺少内容") from exc
        if not isinstance(content, str):
            raise ProviderError("文本模型响应缺少内容")
        return parse_json_loose(content)


class DeepSeekLLM(OpenAILLM):
    """DeepSeek Chat Completions。默认模型 deepseek-flash，即 DeepSeek-V4.1-Flash。

    拆剧本和写分镜只要 JSON，关闭默认的思考模式，避免把推理过程算进耗时。
    文档：https://api-docs.deepseek.com/
    """

    name = "deepseek"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        key = settings.deepseek_api_key.strip()
        if not key:
            raise ProviderError("未配置 DEEPSEEK_API_KEY")
        model = settings.llm_model.strip() or "deepseek-flash"
        self._setup(
            settings,
            key,
            settings.resolved_deepseek_base_url(),
            model,
            client,
            extra={"thinking": {"type": "disabled"}},
        )

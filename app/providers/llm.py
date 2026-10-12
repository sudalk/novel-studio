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
        if not settings.openai_api_key:
            raise ProviderError("未配置 OPENAI_API_KEY")
        self.settings = settings
        self._client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0))
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def complete_json(self, system: str, user: str) -> dict:
        url = self.settings.openai_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        base_payload = {
            "model": self.settings.llm_model,
            "temperature": 0.4,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        payload = {**base_payload, "response_format": {"type": "json_object"}}
        response = self._client.post(url, headers=headers, json=payload)
        if response.status_code == 400:
            response = self._client.post(url, headers=headers, json=base_payload)
        if response.status_code >= 400:
            raise ProviderError(f"文本模型请求失败（{response.status_code}）：{response.text[:400]}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("文本模型响应缺少内容") from exc
        return parse_json_loose(content)

import httpx

from app.config import Settings
from app.providers.errors import ProviderError
from app.providers.image import MockImageProvider, OpenAIImageProvider
from app.providers.llm import DeepSeekLLM, MockLLM, OpenAILLM
from app.providers.tts import MockTTS, MosiTTSProvider


def build_llm(settings: Settings, client: httpx.Client | None = None):
    name = settings.resolved_llm()
    if name == "mock":
        return MockLLM()
    if name == "openai":
        return OpenAILLM(settings, client=client)
    if name == "deepseek":
        return DeepSeekLLM(settings, client=client)
    raise ProviderError(f"未知的文本模型提供方：{name}")


def build_tts(settings: Settings, client: httpx.Client | None = None):
    name = settings.resolved_tts()
    if name == "mock":
        return MockTTS(settings)
    if name == "mosi":
        return MosiTTSProvider(settings, client=client)
    raise ProviderError(f"未知的语音提供方：{name}")


def build_image(settings: Settings, client: httpx.Client | None = None):
    name = settings.resolved_image()
    if name == "mock":
        return MockImageProvider()
    if name == "openai":
        return OpenAIImageProvider(settings, client=client)
    raise ProviderError(f"未知的图像提供方：{name}")


def close_provider(provider) -> None:
    close = getattr(provider, "close", None)
    if callable(close):
        close()

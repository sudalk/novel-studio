import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

# 测试进程里不保留真实密钥。提供方也固定为 mock，避免误打到 MOSI、图像或 DeepSeek。
_SECRET_ENV = (
    "MOSI_API_KEY",
    "MOSS_API_KEY",
    "IMAGE_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
)


@pytest.fixture(autouse=True)
def _pin_tests_to_mock(monkeypatch):
    for name in _SECRET_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_MODE", "mock")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("TTS_PROVIDER", "mock")
    monkeypatch.setenv("IMAGE_PROVIDER", "mock")


def _blank_secrets() -> dict[str, str]:
    return {name.lower(): "" for name in _SECRET_ENV}


@pytest.fixture
def settings(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    return Settings(
        app_mode="mock",
        jobs_inline=True,
        llm_provider="mock",
        tts_provider="mock",
        image_provider="mock",
        data_dir=str(data),
        database_url=f"sqlite:///{data / 'studio.db'}",
        video_width=640,
        video_height=360,
        video_fps=8,
        video_preset="ultrafast",
        mock_tts_min_sec=0.35,
        mock_tts_sec_per_char=0.01,
        mock_tts_max_sec=0.5,
        **_blank_secrets(),
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client

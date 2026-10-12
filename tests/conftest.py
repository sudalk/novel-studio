import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


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
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client

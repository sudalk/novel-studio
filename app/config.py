from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_mode: str = "mock"
    llm_provider: str = ""
    tts_provider: str = ""
    image_provider: str = ""

    database_url: str = "sqlite:///./data/studio.db"
    data_dir: str = "./data"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    image_api_key: str = ""
    image_base_url: str = ""
    image_model: str = "dall-e-3"
    image_size: str = "1792x1024"

    moss_api_key: str = ""
    moss_base_url: str = "https://api.mosi.cn"
    moss_model: str = "moss-ttsd-1.0"
    moss_response_format: str = "wav"
    moss_delivery_method: str = "url"
    moss_async: str = "auto"

    video_width: int = 1280
    video_height: int = 720
    video_fps: int = 24
    video_preset: str = "veryfast"

    mock_tts_min_sec: float = 0.6
    mock_tts_sec_per_char: float = 0.12
    mock_tts_max_sec: float = 2.4

    jobs_inline: bool = False

    @field_validator("data_dir")
    @classmethod
    def _absolute_data_dir(cls, value: str) -> str:
        return str(Path(value).expanduser().resolve())

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    def resolved_llm(self) -> str:
        return self._resolve(self.llm_provider, "openai")

    def resolved_tts(self) -> str:
        return self._resolve(self.tts_provider, "mosi")

    def resolved_image(self) -> str:
        return self._resolve(self.image_provider, "openai")

    def _resolve(self, explicit: str, live_default: str) -> str:
        if explicit and explicit.strip():
            return explicit.strip().lower()
        if self.app_mode.strip().lower() == "mock":
            return "mock"
        return live_default


@lru_cache
def get_settings() -> Settings:
    return Settings()

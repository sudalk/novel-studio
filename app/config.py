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
    # 留空时按提供方取默认：deepseek 为 deepseek-flash，其余为 gpt-4o-mini。
    llm_model: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"

    image_api_key: str = ""
    image_base_url: str = "https://api.aicoming.top/v1"
    image_model: str = "gpt-image-2.5-flare"
    image_aspect: str = "16:9"
    image_size: str = ""
    image_timeout_sec: float = 300
    image_gap_sec: float = 12
    image_max_attempts: int = 6
    image_retry_wait_sec: float = 48

    mosi_api_key: str = ""
    moss_api_key: str = ""
    moss_base_url: str = "https://api.mosi.cn"
    moss_model: str = "moss-ttsd-1.0"
    moss_response_format: str = "wav"
    moss_delivery_method: str = "url"
    moss_async: str = "auto"
    moss_gap_sec: float = 1.5
    moss_max_attempts: int = 4
    moss_retry_wait_sec: float = 8

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

    def resolved_llm_model(self) -> str:
        explicit = (self.llm_model or "").strip()
        if explicit:
            return explicit
        if self.resolved_llm() == "deepseek":
            return "deepseek-flash"
        return "gpt-4o-mini"

    def resolved_llm_key(self) -> str:
        provider = self.resolved_llm()
        if provider == "deepseek":
            return self.deepseek_api_key.strip()
        if provider == "openai":
            return self.openai_api_key.strip()
        return ""

    def resolved_deepseek_base_url(self) -> str:
        return (self.deepseek_base_url or "https://api.deepseek.com").rstrip("/")

    def resolved_tts(self) -> str:
        return self._resolve(self.tts_provider, "mosi")

    def resolved_mosi_key(self) -> str:
        return (self.mosi_api_key or self.moss_api_key).strip()

    def resolved_image(self) -> str:
        return self._resolve(self.image_provider, "openai")

    def resolved_image_base_url(self) -> str:
        return (self.image_base_url or "https://api.aicoming.top/v1").rstrip("/")

    def resolved_image_model(self) -> str:
        return (self.image_model or "gpt-image-2.5-flare").strip()

    def resolved_image_aspect(self) -> str:
        aspect = (self.image_aspect or "16:9").strip().replace("：", ":")
        return aspect or "16:9"

    def resolved_image_size(self) -> str:
        from app.services.image_prompt import size_for_aspect

        explicit = (self.image_size or "").strip().lower().replace("×", "x").replace(" ", "")
        if explicit:
            return explicit
        return size_for_aspect(self.resolved_image_aspect())

    def _resolve(self, explicit: str, live_default: str) -> str:
        if explicit and explicit.strip():
            return explicit.strip().lower()
        if self.app_mode.strip().lower() == "mock":
            return "mock"
        return live_default


@lru_cache
def get_settings() -> Settings:
    return Settings()

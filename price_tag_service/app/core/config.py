from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AismEndpointConfig(BaseSettings):
    api_url: str = ""
    appid: str = ""
    secret: SecretStr = SecretStr("")
    timeout_sec: float = 60
    retries: int = 1
    model_name: str = "AISM_WORKFLOW"
    prompt_version: str = "legacy-directory"


class OssConfig(BaseSettings):
    upload_url: str = ""
    bearer_token: SecretStr = SecretStr("")
    timeout_sec: float = 30
    retries: int = 1


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PRICE_SERVICE_",
        extra="ignore",
    )

    environment: Literal["local", "test", "production"] = "local"
    database_url: str = "sqlite:///./price_tag_service.dev.db"
    api_key: SecretStr = SecretStr("")
    require_api_key: bool = False
    upload_dir: Path = Path("./uploads")
    prompt_dir: Path = Path("prompts")
    worker_concurrency: int = Field(default=4, ge=1, le=8)
    worker_poll_interval_sec: float = Field(default=1.0, ge=0.1)
    worker_max_retries: int = Field(default=3, ge=0)
    worker_retry_backoff_sec: list[float] = Field(default=[2, 4, 8])
    worker_lease_sec: int = Field(default=600, ge=30)
    photo_sources: list[str] = Field(default=["priceTagPhotos", "productCloseupPhotos"])
    max_photos_per_task: int = Field(default=100, ge=1)
    max_upload_mb: int = Field(default=20, ge=1)
    min_price: float | None = 2.0
    max_price: float | None = 99.0
    legacy_config_file: Path = Path("config.json")
    database_echo: bool = False
    sku_sample_dir: Path = Path(__file__).resolve().parents[3] / "sku_match_offline" / "sku_sample_responses"

    qc_config: AismEndpointConfig = Field(default_factory=AismEndpointConfig)
    price_tag_config: AismEndpointConfig = AismEndpointConfig(
        timeout_sec=360, retries=1, model_name="AISM_PRICE_WORKFLOW"
    )
    oss_config: OssConfig = Field(default_factory=OssConfig)


def _merge_legacy_config(settings: Settings, config_path: Path) -> Settings:
    if not config_path.is_file():
        return settings

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    qc_raw = dict(raw.get("qc_config") or {})
    price_raw = dict(raw.get("price_tag_config") or {})
    oss_raw = dict(raw.get("oss_config") or {})
    qc_raw.pop("timeout_sec", None)
    qc_raw.pop("retries", None)
    price_raw.pop("timeout_sec", None)
    price_raw.pop("retries", None)
    oss_raw.pop("timeout_sec", None)
    oss_raw.pop("retries", None)

    settings.qc_config = AismEndpointConfig(**qc_raw)
    settings.price_tag_config = AismEndpointConfig(
        timeout_sec=settings.price_tag_config.timeout_sec,
        retries=settings.price_tag_config.retries,
        model_name=settings.price_tag_config.model_name,
        prompt_version=settings.price_tag_config.prompt_version,
        **price_raw,
    )
    settings.oss_config = OssConfig(
        timeout_sec=settings.oss_config.timeout_sec,
        retries=settings.oss_config.retries,
        **oss_raw,
    )
    return settings


def load_settings() -> Settings:
    settings = Settings()
    return _merge_legacy_config(settings, settings.legacy_config_file)


def redact_secret(value: SecretStr | str | None) -> str:
    if value is None or value == "":
        return ""
    raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
    if len(raw) <= 8:
        return "***"
    return f"{raw[:4]}***{raw[-4:]}"

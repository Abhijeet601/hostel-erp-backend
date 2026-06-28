from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field("MMC Hostel ERP API", validation_alias="APP_NAME")
    debug: bool = Field(False, validation_alias="APP_DEBUG")
    database_url: str = Field(..., validation_alias="DATABASE_URL")
    allowed_origins: str = Field(
        "http://localhost:5500,http://127.0.0.1:5500,http://localhost:8000",
        validation_alias="ALLOWED_ORIGINS",
    )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

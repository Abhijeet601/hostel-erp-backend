from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_mysql_url(value: str) -> str:
    database_url = (value or "").strip()
    if database_url.startswith("mysql://"):
        return database_url.replace("mysql://", "mysql+pymysql://", 1)
    return database_url


class Settings(BaseSettings):
    app_name: str = Field("MMC Hostel ERP API", validation_alias="APP_NAME")
    debug: bool = Field(False, validation_alias="APP_DEBUG")
    database_url: str = Field("", validation_alias="DATABASE_URL")
    mysql_url: str = Field("", validation_alias="MYSQL_URL")
    mysql_public_url: str = Field("", validation_alias="MYSQL_PUBLIC_URL")
    public_base_url: str = Field("", validation_alias="PUBLIC_BASE_URL")
    railway_public_domain: str = Field("", validation_alias="RAILWAY_PUBLIC_DOMAIN")
    allowed_origins: str = Field(
        "http://localhost:5500,http://127.0.0.1:5500,http://localhost:8000",
        validation_alias="ALLOWED_ORIGINS",
    )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def sqlalchemy_database_url(self) -> str:
        database_url = self.database_url or self.mysql_url or self.mysql_public_url
        if not database_url:
            raise ValueError("Set DATABASE_URL, MYSQL_URL, or MYSQL_PUBLIC_URL.")
        return normalize_mysql_url(database_url)

    @property
    def base_url(self) -> str:
        if self.public_base_url:
            return self.public_base_url.rstrip("/")
        if self.railway_public_domain:
            return f"https://{self.railway_public_domain.strip('/')}"
        return "http://127.0.0.1:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()

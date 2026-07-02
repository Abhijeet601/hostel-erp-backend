from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_mysql_url(value: str) -> str:
    database_url = (value or "").strip()
    if database_url.startswith("mysql://"):
        return database_url.replace("mysql://", "mysql+pymysql://", 1)
    return database_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# These are the first-party origins that serve the static Hostel ERP portal.
# Keep them separate from ALLOWED_ORIGIN_REGEX so an older Railway environment
# variable cannot accidentally lock the production website out of the API.
FIRST_PARTY_ORIGIN_REGEX = (
    r"^https://(www\.)?magadhmahilacollege\.org$"
    r"|^https://[^/]+\.vercel\.app$"
    r"|^https://[^/]+\.netlify\.app$"
)


class Settings(BaseSettings):
    app_name: str = Field("MMC Hostel ERP API", validation_alias="APP_NAME")
    debug: bool = Field(False, validation_alias="APP_DEBUG")
    database_url: str = Field("", validation_alias="DATABASE_URL")
    mysql_url: str = Field("", validation_alias="MYSQL_URL")
    mysql_public_url: str = Field("", validation_alias="MYSQL_PUBLIC_URL")
    public_base_url: str = Field("", validation_alias="PUBLIC_BASE_URL")
    hostel_erp_frontend_return_url: str = Field(
        "https://magadhmahilacollege.org/mmc-erp/student/receipt.html",
        validation_alias="HOSTEL_ERP_FRONTEND_RETURN_URL",
    )
    railway_public_domain: str = Field("", validation_alias="RAILWAY_PUBLIC_DOMAIN")
    hostel_erp_data_dir: str = Field("mmc-uploads/hostel erp data", validation_alias="HOSTEL_ERP_DATA_DIR")
    hostel_erp_receipt_dir: str = Field("", validation_alias="HOSTEL_ERP_RECEIPT_DIR")
    allowed_origins: str = Field(
        "null,http://localhost:3000,http://127.0.0.1:3000,http://localhost:5500,http://127.0.0.1:5500,http://localhost:8000,http://127.0.0.1:8000",
        validation_alias="ALLOWED_ORIGINS",
    )
    allowed_origin_regex: str = Field(
        r"^https?://(localhost|127\.0\.0\.1)(:[0-9]+)?$|^https://.*\.up\.railway\.app$",
        validation_alias="ALLOWED_ORIGIN_REGEX",
    )
    r2_account_id: str = Field("", validation_alias="R2_ACCOUNT_ID")
    r2_access_key_id: str = Field("", validation_alias="R2_ACCESS_KEY_ID")
    r2_secret_access_key: str = Field("", validation_alias="R2_SECRET_ACCESS_KEY")
    r2_bucket_name: str = Field("mmc-erp-files", validation_alias="R2_BUCKET_NAME")
    r2_public_url: str = Field("", validation_alias="R2_PUBLIC_URL")
    ccavenue_merchant_id: str = Field("", validation_alias="CCAVENUE_MERCHANT_ID")
    ccavenue_access_code: str = Field("", validation_alias="CCAVENUE_ACCESS_CODE")
    ccavenue_working_key: str = Field("", validation_alias="CCAVENUE_WORKING_KEY")
    ccavenue_gateway_url: str = Field(
        "https://secure.ccavenue.com/transaction/transaction.do?command=initiateTransaction",
        validation_alias="CCAVENUE_GATEWAY_URL",
    )
    ccavenue_currency: str = Field("INR", validation_alias="CCAVENUE_CURRENCY")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def cors_origin_regex(self) -> str:
        configured = self.allowed_origin_regex.strip()
        if not configured:
            return FIRST_PARTY_ORIGIN_REGEX
        return f"(?:{configured})|(?:{FIRST_PARTY_ORIGIN_REGEX})"

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

    def resolve_storage_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return PROJECT_ROOT / path

    @property
    def data_dir_path(self) -> Path:
        return self.resolve_storage_path(self.hostel_erp_data_dir)

    @property
    def receipt_dir_path(self) -> Path:
        if self.hostel_erp_receipt_dir:
            return self.resolve_storage_path(self.hostel_erp_receipt_dir)
        return self.data_dir_path / "receipts"

    @property
    def r2_endpoint_url(self) -> str:
        if self.r2_account_id:
            return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"
        return ""

    @property
    def r2_enabled(self) -> bool:
        return bool(self.r2_account_id and self.r2_access_key_id and self.r2_secret_access_key)

    @property
    def r2_public_base_url(self) -> str:
        if self.r2_public_url:
            return self.r2_public_url.rstrip("/")
        return ""

    @property
    def ccavenue_enabled(self) -> bool:
        return bool(self.ccavenue_merchant_id and self.ccavenue_access_code and self.ccavenue_working_key)



@lru_cache
def get_settings() -> Settings:
    return Settings()

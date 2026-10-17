from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://datapilot:datapilot_local@localhost:5432/datapilot"
    data_dir: Path = ROOT / "data" / "runtime"
    cors_origins: list[str] = ["http://localhost:3000"]
    llm_provider: str = "mock"
    llm_model: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = Field(default="", repr=False)
    llm_timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_dataset_rows: int = Field(default=100_000, gt=0)
    max_dataset_columns: int = Field(default=100, gt=0)
    max_result_rows: int = Field(default=1000, gt=0, le=10_000)
    max_result_bytes: int = Field(default=1024 * 1024, gt=0)
    query_timeout_seconds: float = Field(default=5, gt=0, le=60)

    @field_validator("data_dir")
    @classmethod
    def resolve_data_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else ROOT / value


@lru_cache
def get_settings() -> Settings:
    return Settings()

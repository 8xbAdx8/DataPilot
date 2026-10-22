from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://datapilot:datapilot_local@localhost:5432/datapilot"
    data_dir: Path = ROOT / "data" / "runtime"
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ]
    session_cookie_secure: bool = False
    session_hours: int = Field(default=24, ge=1, le=168)
    auth_trusted_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ]
    llm_provider: str = "mock"
    llm_model: str = "deepseek-flash"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = Field(default="", repr=False)
    llm_timeout_seconds: float = Field(default=30, gt=0, le=120)
    workflow_timeout_seconds: float = Field(default=90, gt=0, le=180)
    sql_repair_enabled: bool = True
    agent_enabled: bool = True
    agent_native_tools: bool = False
    agent_max_model_calls: int = Field(default=3, ge=1, le=4)
    agent_max_tool_calls: int = Field(default=4, ge=1, le=6)
    agent_token_budget: int = Field(default=64000, ge=2000, le=100000)
    embedding_provider: str = "none"
    embedding_model: str = "text-embedding-3-small"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = Field(default="", repr=False)
    embedding_dimensions: int = 1536
    embedding_timeout_seconds: float = Field(default=20, gt=0, le=60)
    rag_top_k: int = Field(default=4, ge=1, le=8)
    rag_min_similarity: float = Field(default=0.70, ge=0, le=1)
    rag_strategy: Literal["vector", "hybrid"] = "vector"
    rag_reranker: Literal["none", "flashrank"] = "none"
    # Candidate compatibility remains default until independent semantic acceptance.
    rag_evidence_policy: Literal["legacy", "registered"] = "legacy"
    rag_experiment_mode: Literal["off", "shadow", "enabled"] = "off"
    rag_experiment_synthetic: bool = False
    reranker_min_score: float = Field(default=0.5, ge=0, le=1)
    reranker_timeout_seconds: float = Field(default=15, gt=0, le=60)
    reranker_cache_dir: Path = ROOT / "data/runtime/flashrank"
    reranker_python: Path | None = None

    @field_validator("reranker_python", mode="before")
    @classmethod
    def optional_python(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def compatible_ranking(self):
        if self.rag_reranker == "flashrank" and self.rag_strategy != "hybrid":
            raise ValueError("FlashRank requires RAG_STRATEGY=hybrid")
        if self.rag_experiment_mode != "off" and (
            not self.rag_experiment_synthetic or self.datapilot_langsmith_enabled
        ):
            raise ValueError("Experimental retrieval requires synthetic isolation and tracing off")
        return self

    knowledge_requests_per_window: int = Field(default=10, ge=1, le=120)
    datapilot_langsmith_enabled: bool = False
    langsmith_api_key: str = Field(default="", repr=False)
    langsmith_project: str = Field(default="datapilot-metadata", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    @field_validator("langsmith_endpoint")
    @classmethod
    def safe_trace_endpoint(cls, value):
        from urllib.parse import urlparse

        url = urlparse(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("LangSmith 地址必须使用 HTTPS，且不能包含凭据、查询参数或片段。")
        return value.rstrip("/")

    @field_validator("embedding_dimensions")
    @classmethod
    def fixed_embedding_dimension(cls, value):
        if value != 1536:
            raise ValueError("知识向量固定为 1536 维；变更维度需数据库迁移及全量重新嵌入。")
        return value

    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_dataset_rows: int = Field(default=100_000, gt=0)
    max_dataset_columns: int = Field(default=100, gt=0)
    max_result_rows: int = Field(default=1000, gt=0, le=10_000)
    max_result_bytes: int = Field(default=1024 * 1024, gt=0)
    query_timeout_seconds: float = Field(default=5, gt=0, le=60)
    analysis_requests_per_window: int = Field(default=30, ge=1, le=1000)
    upload_requests_per_window: int = Field(default=10, ge=1, le=1000)
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)

    @field_validator("data_dir", "reranker_cache_dir")
    @classmethod
    def resolve_data_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else ROOT / value


@lru_cache
def get_settings() -> Settings:
    return Settings()

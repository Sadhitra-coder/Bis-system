from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
MARKDOWN_DATA_DIR = DATA_DIR / "markdown"
CLEANED_DATA_DIR = DATA_DIR / "cleaned"
STRUCTURED_DATA_DIR = DATA_DIR / "structured"
NORMALIZED_DATA_DIR = DATA_DIR / "normalized"
CHUNKS_DATA_DIR = DATA_DIR / "chunks"


class Settings(BaseSettings):
    """Application-wide configuration for the BIS GPT lazy-ingestion system."""

    PROJECT_NAME: str = "BIS RAG Engine"
    BIS_BASE_URL: str = "https://www.bis.gov.in"
    DATABASE_URL: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/bis_rag"
    )
    POSTGRES_DB: str = "bis_rag"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    REQUEST_TIMEOUT: int = 20
    MAX_CRAWL_DEPTH: int = 3
    CRAWL_DELAY: float = 0.5
    MAX_RETRIEVAL_LOOPS: int = 2
    DOCUMENT_MAX_SIZE: int = 25 * 1024 * 1024

    # Retrieval and lazy-ingestion settings
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    VECTOR_TOP_K: int = 10
    RERANK_TOP_K: int = 5
    DOCUMENT_SELECTION_LIMIT: int = 3

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()


def ensure_directories() -> None:
    """Create required project directories if they do not exist."""

    for directory in (
        DATA_DIR,
        RAW_DATA_DIR,
        MARKDOWN_DATA_DIR,
        CLEANED_DATA_DIR,
        STRUCTURED_DATA_DIR,
        NORMALIZED_DATA_DIR,
        CHUNKS_DATA_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
MARKDOWN_DATA_DIR = DATA_DIR / "markdown"


class Settings(BaseSettings):
    """
    Application configuration.
    """

    PROJECT_NAME: str = "BIS RAG Engine"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()


def ensure_directories() -> None:
    """
    Create required project directories if they do not exist.
    """

    RAW_DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    MARKDOWN_DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )
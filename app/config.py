"""
Central application configuration.

This is the SINGLE source of truth for:
    - project directory layout
    - runtime settings loaded from environment / .env

No other module should call os.getenv() for these values.
Import `settings` instead.
"""

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


# ============================================================
# DIRECTORY LAYOUT
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"

# Ingestion stages (one directory per pipeline stage)
RAW_DATA_DIR = DATA_DIR / "raw"
MARKDOWN_DATA_DIR = DATA_DIR / "markdown"
CLEANED_DATA_DIR = DATA_DIR / "cleaned"
STRUCTURED_DATA_DIR = DATA_DIR / "structured"
NORMALIZED_DATA_DIR = DATA_DIR / "normalized"
CHUNKS_DATA_DIR = DATA_DIR / "chunks"

# Vector index
VECTOR_DB_DIR = DATA_DIR / "vector_db"

# Uploaded files land in data/raw/uploads/
UPLOAD_DIR = RAW_DATA_DIR / "uploads"

# Ingestion registry tracking file hashes and status
REGISTRY_FILE = DATA_DIR / "ingestion_registry.json"


ALL_DATA_DIRS = (
    RAW_DATA_DIR,
    MARKDOWN_DATA_DIR,
    CLEANED_DATA_DIR,
    STRUCTURED_DATA_DIR,
    NORMALIZED_DATA_DIR,
    CHUNKS_DATA_DIR,
    VECTOR_DB_DIR,
    UPLOAD_DIR,
)


# ============================================================
# SETTINGS
# ============================================================

class Settings(BaseSettings):
    """
    Runtime configuration.

    Every value can be overridden by an environment variable
    of the same name, or by an entry in the project .env file.
    """

    # ---------------- Application ----------------
    PROJECT_NAME: str = "BIS RAG Engine"
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # ---------------- Service-to-Service Security ----------------
    INTERNAL_SERVICE_KEY: Optional[str] = "complywise-internal-bis-key-default"

    # ---------------- Groq / LLM ----------------
    # Structuring (ingestion) and answer generation (RAG) both
    # use Groq. They may run on different models.
    GROQ_API_KEY: Optional[str] = None

    # Model used by app/steps/structure.py
    GROQ_STRUCTURE_MODEL: str = "openai/gpt-oss-120b"

    # Model used by app/rag/generator.py
    GROQ_MODEL: str = "openai/gpt-oss-20b"

    # Master switch for the LLM structuring stage.
    LLM_ENABLED: bool = True

    # Master switch for optional LLM intent classification (Phase 10)
    ENABLE_LLM_INTENT_CLASSIFIER: bool = False

    # Retry / backoff for all Groq calls.
    GROQ_MAX_RETRIES: int = 3
    GROQ_RETRY_BASE_DELAY: float = 1.0
    GROQ_RETRY_MAX_DELAY: float = 20.0

    # When True, a batch that still fails after all retries
    # falls back to the deterministic Python classifier.
    # When False, the failure is raised and ingestion stops.
    # Default False: an API failure must not silently become
    # "successful" heuristic output.
    STRUCTURE_ALLOW_FALLBACK: bool = False

    # ---------------- Structuring ----------------
    STRUCTURE_BATCH_SIZE: int = 20
    STRUCTURE_MAX_OUTPUT_TOKENS: int = 8000

    # ---------------- Chunking ----------------
    MAX_CHUNK_CHARS: int = 4000
    MIN_CHUNK_CHARS: int = 250

    # ---------------- Embedding / vector index ----------------
    EMBEDDING_MODEL: str = "BAAI/bge-large-en-v1.5"
    EMBEDDING_BATCH_SIZE: int = 16
    CHROMA_COLLECTION_NAME: str = "bis_documents"

    # ---------------- Retrieval ----------------
    RERANKER_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # Weight of the dense score in hybrid fusion (legacy; not used by RRF).
    HYBRID_ALPHA: float = 0.65

    RETRIEVAL_TOP_K: int = 10
    DENSE_K: int = 15
    BM25_K: int = 15
    RERANK_TOP_K: int = 5

    # Reciprocal Rank Fusion constant (higher = less steep rank discounting)
    RRF_K: int = 60
    # Maximum candidate pool entering RRF fusion
    RRF_CANDIDATE_LIMIT: int = 100

    # Identifier boost added to RRF score on exact match (fractional, not absolute)
    IDENTIFIER_BOOST_STANDARD: float = 0.20    # exact standard number match
    IDENTIFIER_BOOST_STANDARD_YEAR: float = 0.10  # standard + year
    IDENTIFIER_BOOST_CLAUSE: float = 0.15     # clause number match
    IDENTIFIER_BOOST_AMENDMENT: float = 0.10  # amendment number match

    # ---------------- Contextual Retrieval (Phase 5) ----------------
    ENABLE_CONTEXTUAL_RETRIEVAL: bool = True
    CONTEXT_GENERATION_METHOD: str = "structural"  # "structural" | "llm"
    ENABLE_LLM_CONTEXTUALIZATION: bool = False     # feature flag for optional LLM mode
    MAX_CONTEXT_CHARS: int = 400                   # maximum length of contextual prefix
    CONTEXT_GENERATION_VERSION: str = "1.0"

    # Bounds for the optional LLM context path. The preface is one sentence,
    # so both are deliberately small: the passage is sent only to identify a
    # topic, not to be summarised, and one call runs per chunk.
    LLM_CONTEXT_INPUT_CHARS: int = 1500   # passage characters sent to the model
    LLM_CONTEXT_MAX_TOKENS: int = 120     # ceiling on the generated preface


    # ---------------- Generation ----------------
    GENERATION_TEMPERATURE: float = 0.1

    GENERATION_MAX_TOKENS: int = 1200

    # ---------------- API limits (Phase 6 section 18) ----------------
    #
    # SECURITY POSTURE: these bound resource consumption only. The API has
    # NO AUTHENTICATION on any endpoint — anyone who can reach the port can
    # ingest documents and spend Groq credits. That remains a known
    # production blocker and is deliberately NOT addressed here; see the
    # Phase 6 report, section U.

    # Largest accepted upload. Docling holds a converted document in memory,
    # so an unbounded upload is an unbounded allocation.
    MAX_UPLOAD_SIZE_MB: int = 50

    # Concurrent background ingestions. Each one loads a Docling converter
    # and may call the structuring LLM, so unbounded concurrency turns a
    # burst of uploads into memory exhaustion. Requests beyond the limit are
    # rejected with 429 rather than queued indefinitely.
    MAX_CONCURRENT_INGESTIONS: int = 2

    # Upper bound on a caller-supplied /query top_k.
    MAX_QUERY_TOP_K: int = 50

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # --------------------------------------------------------
    # DERIVED HELPERS
    # --------------------------------------------------------

    @property
    def llm_available(self) -> bool:
        """
        True only when the structuring LLM is both enabled
        and actually usable (an API key is present).
        """
        return bool(self.LLM_ENABLED and self.GROQ_API_KEY)


settings = Settings()


# ============================================================
# DIRECTORY CREATION
# ============================================================

def ensure_directories() -> None:
    """
    Create every project data directory if it does not exist.
    """

    for directory in ALL_DATA_DIRS:
        directory.mkdir(parents=True, exist_ok=True)

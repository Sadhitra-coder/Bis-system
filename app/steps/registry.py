"""
Hash-based document ingestion registry for idempotency.

Tracks every ingested file by its SHA-256 digest so:
  1. Identical file contents are detected and skipped without duplicating chunks.
  2. Changed file contents are detected (new hash) and re-ingested.
  3. Different files with identical original filenames are isolated safely.
"""

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import REGISTRY_FILE

logger = logging.getLogger(__name__)

_registry_lock = threading.Lock()


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


class IngestionRegistry:
    """Persistent registry of ingested document content hashes."""

    def __init__(self, registry_file: Path = REGISTRY_FILE):
        self.registry_file = Path(registry_file)
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self.registry_file.exists():
            try:
                content = self.registry_file.read_text(encoding="utf-8")
                self._data = json.loads(content)
            except Exception as e:
                logger.warning("Could not read ingestion registry: %s. Starting fresh.", e)
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        try:
            self.registry_file.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.registry_file.with_suffix(".tmp")
            temp_file.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            temp_file.replace(self.registry_file)
        except Exception as e:
            logger.error("Failed to save ingestion registry: %s", e)

    def get_entry(self, content_hash: str) -> Optional[Dict[str, Any]]:
        """Return registry record for a content hash if already ingested."""
        with _registry_lock:
            return self._data.get(content_hash)

    def register(
        self,
        content_hash: str,
        document_id: str,
        filename: str,
        chunks_indexed: int,
        outputs: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Record a successful ingestion."""
        with _registry_lock:
            record = {
                "content_hash": content_hash,
                "document_id": document_id,
                "filename": filename,
                "chunks_indexed": chunks_indexed,
                "outputs": outputs or {},
                "ingested_at": time.time()
            }
            self._data[content_hash] = record
            self._save()
            return record


registry = IngestionRegistry()

"""app/release/manifest.py

Immutable Corpus Release Manifest Generator & Validator.

Ensures that local and Azure data environments remain strictly reproducible
through versioned, content-hashed corpus releases (e.g., corpus-release-0001).
"""

import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.config import DATA_DIR, RAW_DATA_DIR, VECTOR_DB_DIR

logger = logging.getLogger(__name__)

RELEASES_DIR = DATA_DIR / "releases"
DEFAULT_DB_PATH = DATA_DIR / "knowledge" / "bis_knowledge.db"


class DocumentManifestEntry(BaseModel):
    document_id: str
    filename: str
    standard_number: str
    title: str
    sha256: str
    size_bytes: int
    rights_status: str


class CorpusManifest(BaseModel):
    release_id: str
    manifest_version: str = "1.0.0"
    created_at: str
    git_commit: str
    description: str = "Immutable BIS Intelligence Engine Corpus Release"
    source_sync_timestamp: str

    metrics: Dict[str, int] = Field(default_factory=dict)
    checksums: Dict[str, str] = Field(default_factory=dict)
    rights_summary: Dict[str, int] = Field(default_factory=dict)
    documents: List[DocumentManifestEntry] = Field(default_factory=list)
    sectors_covered: List[str] = Field(default_factory=list)

    def to_file(self, filepath: Path) -> None:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(self.model_dump_json(indent=2))


def _compute_sha256(filepath: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _get_git_commit() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(DATA_DIR.parent),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            return res.stdout.strip()[:10]
    except Exception:
        pass
    return "unknown"


def create_corpus_release(
    release_id: Optional[str] = None,
    description: str = "Official BIS Knowledge Base Immutable Release",
    db_path: Optional[Path] = None,
) -> CorpusManifest:
    """
    Assembles a new immutable Corpus Release from current SQLite and ChromaDB data.
    """
    target_db = Path(db_path) if db_path else DEFAULT_DB_PATH
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)

    # Determine next release ID if not provided
    if not release_id:
        existing = list_releases()
        next_num = len(existing) + 1
        release_id = f"corpus-release-{next_num:04d}"

    now_iso = datetime.now(timezone.utc).isoformat()
    git_sha = _get_git_commit()

    # 1. Query SQLite Metrics
    metrics: Dict[str, int] = {}
    rights_summary: Dict[str, int] = {}
    sectors: List[str] = []

    if target_db.exists():
        with sqlite3.connect(str(target_db)) as conn:
            conn.row_factory = sqlite3.Row
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
            for t in tables:
                metrics[f"{t}_count"] = conn.execute(f"SELECT count(*) FROM {t};").fetchone()[0]

            # Rights breakdown from discovered_documents
            if "discovered_documents" in tables:
                r_rows = conn.execute("SELECT rights_status, count(*) as cnt FROM discovered_documents GROUP BY rights_status;").fetchall()
                for r in r_rows:
                    rights_summary[r["rights_status"]] = r["cnt"]

            # Industrial sectors from standards_metadata_catalog
            if "standards_metadata_catalog" in tables:
                s_rows = conn.execute("SELECT DISTINCT category FROM standards_metadata_catalog WHERE category IS NOT NULL;").fetchall()
                sectors = sorted([r["category"] for r in s_rows])

    # 2. ChromaDB Chunk Count
    chunks_count = 0
    chroma_sqlite = VECTOR_DB_DIR / "chroma.sqlite3"
    if chroma_sqlite.exists():
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(VECTOR_DB_DIR))
            for col in client.list_collections():
                chunks_count += col.count()
        except Exception:
            # Fallback to direct sqlite query on chroma.sqlite3
            try:
                with sqlite3.connect(str(chroma_sqlite)) as cconn:
                    chunks_count = cconn.execute("SELECT count(*) FROM embeddings;").fetchone()[0]
            except Exception:
                pass
    metrics["indexed_chunks_count"] = chunks_count

    # 3. Checksums
    checksums: Dict[str, str] = {}
    if target_db.exists():
        checksums["bis_knowledge_db_sha256"] = _compute_sha256(target_db)
    if chroma_sqlite.exists():
        checksums["chroma_sqlite3_sha256"] = _compute_sha256(chroma_sqlite)

    # 4. Catalog downloaded PDFs in data/raw/bis
    documents: List[DocumentManifestEntry] = []
    bis_raw_dir = RAW_DATA_DIR / "bis"
    if bis_raw_dir.exists():
        for doc_dir in sorted(bis_raw_dir.glob("doc_*")):
            if doc_dir.is_dir():
                for pdf in sorted(doc_dir.glob("*.pdf")):
                    sha = _compute_sha256(pdf)
                    size = pdf.stat().st_size
                    doc_id = doc_dir.name
                    std_num = pdf.stem.replace("__", " ").replace("_", " ")

                    documents.append(
                        DocumentManifestEntry(
                            document_id=doc_id,
                            filename=pdf.name,
                            standard_number=std_num,
                            title=f"Official BIS Manual / Specification ({pdf.stem})",
                            sha256=sha,
                            size_bytes=size,
                            rights_status="PUBLIC",
                        )
                    )
    metrics["downloaded_documents_count"] = len(documents)

    # 5. Assemble manifest
    manifest = CorpusManifest(
        release_id=release_id,
        manifest_version="1.0.0",
        created_at=now_iso,
        git_commit=git_sha,
        description=description,
        source_sync_timestamp=now_iso,
        metrics=metrics,
        checksums=checksums,
        rights_summary=rights_summary,
        documents=documents,
        sectors_covered=sectors,
    )

    # 6. Save manifest to data/releases/<release_id>/manifest.json
    release_dir = RELEASES_DIR / release_id
    release_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = release_dir / "manifest.json"
    manifest.to_file(manifest_file)

    logger.info("Successfully created Corpus Release %s at %s", release_id, manifest_file)
    return manifest


def list_releases() -> List[str]:
    """Returns sorted list of available release IDs."""
    if not RELEASES_DIR.exists():
        return []
    releases = []
    for d in RELEASES_DIR.iterdir():
        if d.is_dir() and (d / "manifest.json").exists():
            releases.append(d.name)
    return sorted(releases)


def load_release_manifest(release_id: str) -> Optional[CorpusManifest]:
    """Loads manifest for a specific release ID."""
    manifest_file = RELEASES_DIR / release_id / "manifest.json"
    if not manifest_file.exists():
        return None
    with open(manifest_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return CorpusManifest(**data)


def get_latest_release() -> Optional[CorpusManifest]:
    """Retrieves the latest created release manifest."""
    releases = list_releases()
    if not releases:
        return None
    return load_release_manifest(releases[-1])


def verify_release_integrity(manifest: CorpusManifest, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Verifies that disk files match the release manifest checksums.
    """
    target_db = Path(db_path) if db_path else DEFAULT_DB_PATH
    results = {
        "valid": True,
        "release_id": manifest.release_id,
        "checks": {},
        "errors": [],
    }

    # Verify SQLite DB hash
    expected_db_hash = manifest.checksums.get("bis_knowledge_db_sha256")
    if expected_db_hash:
        if not target_db.exists():
            results["valid"] = False
            results["errors"].append("SQLite database file does not exist")
        else:
            actual_db_hash = _compute_sha256(target_db)
            matches = actual_db_hash == expected_db_hash
            results["checks"]["database_hash_matches"] = matches
            if not matches:
                results["valid"] = False
                results["errors"].append(f"DB hash mismatch: expected {expected_db_hash}, got {actual_db_hash}")

    # Verify downloaded documents
    doc_matches = 0
    for doc in manifest.documents:
        expected_doc_path = RAW_DATA_DIR / "bis" / doc.document_id / doc.filename
        if not expected_doc_path.exists():
            results["valid"] = False
            results["errors"].append(f"Document missing: {expected_doc_path}")
        else:
            actual_hash = _compute_sha256(expected_doc_path)
            if actual_hash == doc.sha256:
                doc_matches += 1
            else:
                results["valid"] = False
                results["errors"].append(f"Document {doc.filename} hash mismatch")

    results["checks"]["documents_verified"] = doc_matches
    results["checks"]["total_documents"] = len(manifest.documents)
    return results

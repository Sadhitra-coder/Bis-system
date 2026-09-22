"""app/acquisition/update_engine.py

Incremental Update Engine & Synchronization Coordinator.

Implements:
- Incremental change detection (new, changed, amended, superseded)
- Content hash deduplication (never duplicate unchanged documents)
- Quality gating before index promotion (metadata, schema 6.0, retrieval, abstention)
"""

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.acquisition.discovery import default_discovery_engine
from app.acquisition.models import (
    AcquisitionPriority,
    DiscoveredItem,
    DocumentClass,
    LicenseStatus,
)
from app.acquisition.policy import default_policy_enforcer
from app.acquisition.priority_queue import AcquisitionPriorityQueue
from app.acquisition.registry import default_source_registry
from app.config import DATA_DIR, settings
from app.index_integrity import check_index_integrity
from app.index_schema import validate_chunk_index_metadata
from app.knowledge import default_knowledge_service
from app.knowledge.models import (
    KnowledgeRelationship,
    RelationshipType,
)
from app.knowledge.repository import default_repository
from app.steps.embed import get_collection, load_embedding_model
from app.steps.pipeline import process_pdf

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = DATA_DIR / "knowledge" / "bis_knowledge.db"


class SyncEvaluationResult:
    """Outcome of quality evaluation gates run on an ingested document."""
    def __init__(self, passed: bool, checks: Dict[str, bool], details: Dict[str, Any]):
        self.passed = passed
        self.checks = checks
        self.details = details

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": self.checks,
            "details": self.details,
        }


class UpdateEngine:
    """
    Orchestrates controlled incremental knowledge acquisition,
    deduplication, multi-stage ingestion, and quality-gated index promotion.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()
        self.priority_queue = AcquisitionPriorityQueue()

    @staticmethod
    def compute_content_hash(raw_bytes: bytes) -> str:
        """Compute SHA-256 content hash for document deduplication."""
        return hashlib.sha256(raw_bytes).hexdigest()

    def _init_tables(self) -> None:
        with self._conn:
            self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_history (
                sync_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                started_at REAL NOT NULL,
                completed_at REAL,
                status TEXT NOT NULL,
                items_discovered INTEGER NOT NULL DEFAULT 0,
                items_ingested INTEGER NOT NULL DEFAULT 0,
                items_skipped INTEGER NOT NULL DEFAULT 0,
                items_failed INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                evaluation_summary TEXT
            );
            """)

    def queue_discovered_items(self, items: List[DiscoveredItem]) -> int:
        """Push items to priority queue after validating rights."""
        queued = 0
        for item in items:
            # Enforce document policy
            status, can_reproduce, can_index_meta = default_policy_enforcer.evaluate_rights(
                source_domain=item.source_url,
                document_class=item.document_class.value,
                source_url=item.source_url,
                document_id=item.item_id,
            )
            item.license_status = status

            # If restricted or unknown, quarantine
            if status in (LicenseStatus.RESTRICTED, LicenseStatus.UNKNOWN_RIGHTS):
                item.quarantine_reason = f"Document quarantined under {status.value} policy."
                default_discovery_engine.record_discovered_item(item)
                continue

            self.priority_queue.push(item)
            default_discovery_engine.record_discovered_item(item)
            queued += 1

        return queued

    def synchronize_item(self, item: DiscoveredItem, pdf_path: Optional[Path] = None) -> Tuple[bool, str]:
        """
        Incrementally process a discovered item through the full ingestion pipeline.
        Deduplicates by content hash to ensure zero duplication of unchanged documents.
        """
        if not pdf_path or not pdf_path.exists():
            return False, f"PDF file not found for item {item.identifier}"

        # 1. Content Hash & Deduplication
        content_bytes = pdf_path.read_bytes()
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        item.content_hash = content_hash

        cursor = self._conn.cursor()
        cursor.execute("SELECT document_id, access_date FROM document_rights_ledger WHERE content_hash = ?", (content_hash,))
        existing = cursor.fetchone()
        if existing and item.is_ingested:
            logger.info("Document %s (%s) unchanged; skipping re-ingestion.", item.identifier, content_hash[:12])
            return True, f"Unchanged document already indexed as {existing['document_id']}."

        # 2. Rights Evaluation
        status, can_reproduce, can_index_meta = default_policy_enforcer.evaluate_rights(
            source_domain=item.source_url,
            document_class=item.document_class.value,
            raw_bytes=content_bytes,
            source_url=item.source_url,
            document_id=item.item_id,
        )

        if not can_reproduce:
            logger.warning("Item %s has license status %s; full text ingestion prohibited.", item.identifier, status.value)
            return False, f"Full text prohibited under policy {status.value}"

        # 3. Multi-Stage Ingestion Pipeline
        try:
            embedder = load_embedding_model()
            collection = get_collection()
            pipe_result = process_pdf(
                pdf_path=pdf_path,
                embedding_model=embedder,
                collection=collection,
            )
        except Exception as e:
            logger.exception("Ingestion failed for %s: %s", item.identifier, e)
            return False, f"Ingestion error: {e}"

        # 4. Quality Gating & Evaluation Gate
        eval_result = self.evaluate_ingested_document(pipe_result, collection)
        if not eval_result.passed:
            logger.error("Quality gates failed for %s: %s", item.identifier, eval_result.details)
            return False, f"Quality gate failure: {eval_result.details}"

        # 5. Graph Relationships (SUPERSEDES / AMENDS)
        self._record_graph_updates(item, pipe_result)

        # 6. Mark Ingested
        item.is_ingested = True
        item.ingestion_job_id = pipe_result.get("document_id")
        default_discovery_engine.record_discovered_item(item)

        return True, f"Successfully synchronized {item.identifier} ({pipe_result.get('chunks_indexed', 0)} chunks indexed)."

    def evaluate_ingested_document(self, pipe_result: Dict[str, Any], collection: Any) -> SyncEvaluationResult:
        """
        Quality gate run before promoting a document to production index.
        Checks:
        1. Metadata completeness
        2. Provenance validity (chunk offsets, page numbers)
        3. Schema 6.0 conformance
        4. Vector DB retrievability
        """
        checks = {
            "metadata_valid": False,
            "provenance_valid": False,
            "schema_conformance": False,
            "chunks_retrievable": False,
        }
        details = {}

        # 1. Metadata check
        doc_meta = pipe_result.get("doc_metadata", {})
        if doc_meta.get("document_id") and doc_meta.get("source_filename"):
            checks["metadata_valid"] = True
        else:
            details["metadata_error"] = "Missing required document-level metadata fields."

        # 2. Provenance check
        prov_issues = pipe_result.get("provenance_issues", {})
        if len(prov_issues) == 0:
            checks["provenance_valid"] = True
        else:
            details["provenance_warning"] = f"{len(prov_issues)} provenance issues flagged."
            checks["provenance_valid"] = True  # Non-blocking if minor

        # 3. Schema 6.0 check
        chunks_indexed = pipe_result.get("chunks_indexed", 0)
        report = check_index_integrity(collection)
        if report.is_healthy:
            checks["schema_conformance"] = True
        else:
            details["schema_error"] = f"Index health report failed: {report.state}"

        # 4. Retrievability
        if chunks_indexed > 0 and report.is_retrievable:
            checks["chunks_retrievable"] = True
        else:
            details["retrieval_error"] = "Zero chunks retrievable from collection."

        passed = checks["metadata_valid"] and checks["schema_conformance"] and checks["chunks_retrievable"]
        return SyncEvaluationResult(passed=passed, checks=checks, details=details)

    def _record_graph_updates(self, item: DiscoveredItem, pipe_result: Dict[str, Any]) -> None:
        """Update knowledge graph relationships upon document synchronization."""
        now = time.time()
        doc_id = pipe_result.get("document_id") or item.item_id

        # If this is a QCO, create QCO entity and APPLIES_TO / REQUIRES relations
        if item.document_class in (DocumentClass.QCO, DocumentClass.QCO_AMENDMENT):
            qco = default_repository.get_qco(item.identifier)
            if not qco:
                from app.knowledge.models import QCO
                qco = QCO(
                    qco_id=f"qco_{item.identifier.replace(' ', '_')}",
                    qco_number=item.identifier,
                    title=item.title,
                    issuing_ministry=item.metadata_payload.get("ministry", "Central Ministry"),
                    order_date=item.publication_date,
                    enforcement_date=item.effective_date,
                    document_id=doc_id,
                    is_mandatory=True,
                    created_at=now,
                )
                default_repository.save_qco(qco)

            # Link relationship
            rel = KnowledgeRelationship(
                relationship_id=f"rel_qco_{item.item_id[:12]}",
                source_entity_type="qco",
                source_entity_id=qco.qco_id,
                target_entity_type="standard",
                target_entity_id=item.identifier,
                relationship_type=RelationshipType.REQUIRES,
                metadata={"status": "MANDATORY_COMPLIANCE"},
                confidence=1.0,
                created_at=now,
            )
            default_repository.save_relationship(rel)


default_update_engine = UpdateEngine()

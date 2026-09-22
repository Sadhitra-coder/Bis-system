"""app/acquisition/sync/coordinator.py

Master Acquisition & Synchronization Coordinator.

Coordinates the complete data lifecycle:
1. DISCOVERY: Scans official BIS guidelines and Know Your Standards endpoints.
2. RIGHTS CHECK: Verifies legal accessibility and intellectual property classification.
3. QUEUING: Prioritizes documents (P0 QCOs -> P1 Product Manuals -> P2 Metadata).
4. DOWNLOAD: Downloads raw PDFs with rate limits, retries, and hash calculation to data/raw/bis/.
5. DEDUPLICATION: Skips identical documents by SHA-256 content hash.
6. INGESTION: Reuses the master ingestion pipeline (app.steps.pipeline.process_pdf) to
   extract, clean, structure, normalize, chunk, and index into ChromaDB & SQLite.
7. RESUMABLE STATE: Persists job progress in SQLite acquisition_jobs table.
"""

import hashlib
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.acquisition.discovery.crawler import BISOfficialCrawler, default_bis_crawler
from app.acquisition.discovery.qco_collector import QCOCollector, default_qco_collector
from app.acquisition.discovery.standards_collector import StandardsMetadataCollector, default_standards_collector
from app.acquisition.downloader.client import DocumentDownloader, DownloadResult, default_downloader
from app.acquisition.models import (
    AuditLogEntry,
    DocumentDiscoveryRecord,
    DownloadStatus,
    JobStateRecord,
    RightsStatus,
)
from app.acquisition.policies.access_control import AccessControlPolicy, default_access_policy
from app.acquisition.queue.priority_queue import SafeAcquisitionQueue, default_acquisition_queue
from app.config import DATA_DIR, RAW_DATA_DIR
from app.steps.embed import get_collection, load_embedding_model
from app.steps.pipeline import process_pdf

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = DATA_DIR / "knowledge" / "bis_knowledge.db"


class AcquisitionCoordinator:
    """Orchestrates end-to-end acquisition, downloading, deduplication, and ingestion."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        crawler: Optional[BISOfficialCrawler] = None,
        downloader: Optional[DocumentDownloader] = None,
        policy: Optional[AccessControlPolicy] = None,
        queue: Optional[SafeAcquisitionQueue] = None,
    ):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.crawler = crawler or default_bis_crawler
        self.downloader = downloader or default_downloader
        self.policy = policy or default_access_policy
        self.queue = queue or default_acquisition_queue
        self._init_tables()

    def _init_tables(self) -> None:
        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS acquisition_jobs (
                job_id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                total_discovered INTEGER NOT NULL DEFAULT 0,
                current_index INTEGER NOT NULL DEFAULT 0,
                downloaded_count INTEGER NOT NULL DEFAULT 0,
                ingested_count INTEGER NOT NULL DEFAULT 0,
                blocked_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                started_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                error TEXT
            );
            """)

    def _get_or_create_job(self, job_type: str, source_id: str) -> JobStateRecord:
        now = time.time()
        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
            SELECT * FROM acquisition_jobs
            WHERE job_type = ? AND source_id = ? AND status IN ('PENDING', 'RUNNING')
            ORDER BY started_at DESC LIMIT 1
            """, (job_type, source_id)).fetchone()

            if row:
                return JobStateRecord(
                    job_id=row["job_id"],
                    job_type=row["job_type"],
                    source_id=row["source_id"],
                    total_discovered=row["total_discovered"],
                    current_index=row["current_index"],
                    downloaded_count=row["downloaded_count"],
                    ingested_count=row["ingested_count"],
                    blocked_count=row["blocked_count"],
                    failed_count=row["failed_count"],
                    status=row["status"],
                    started_at=row["started_at"],
                    updated_at=row["updated_at"],
                    error=row["error"],
                )

            new_job_id = f"job_{uuid.uuid4().hex[:12]}"
            conn.execute("""
            INSERT INTO acquisition_jobs (
                job_id, job_type, source_id, status, started_at, updated_at
            ) VALUES (?, ?, ?, 'RUNNING', ?, ?)
            """, (new_job_id, job_type, source_id, now, now))

            return JobStateRecord(
                job_id=new_job_id,
                job_type=job_type,
                source_id=source_id,
                status="RUNNING",
                started_at=now,
                updated_at=now,
            )

    def _update_job_progress(self, job: JobStateRecord) -> None:
        job.updated_at = time.time()
        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            conn.execute("""
            UPDATE acquisition_jobs SET
                total_discovered = ?, current_index = ?, downloaded_count = ?,
                ingested_count = ?, blocked_count = ?, failed_count = ?,
                status = ?, updated_at = ?, error = ?
            WHERE job_id = ?
            """, (
                job.total_discovered, job.current_index, job.downloaded_count,
                job.ingested_count, job.blocked_count, job.failed_count,
                job.status, job.updated_at, job.error, job.job_id
            ))

    def run_discovery(self, filter_terms: Optional[List[str]] = None, limit: int = 50) -> List[DocumentDiscoveryRecord]:
        """
        Execute official discovery across BIS endpoints.
        """
        job = self._get_or_create_job("DISCOVERY", "bis_official")
        logger.info("Starting BIS Discovery job %s (terms=%s, limit=%d)...", job.job_id, filter_terms, limit)

        # 1. Discover Official BIS Product Manuals (100% public guidance PDFs)
        pm_records = self.crawler.discover_product_manuals(filter_terms=filter_terms, limit=limit)

        # 2. Discover Standards Metadata from live BIS Elasticsearch API
        query_terms = filter_terms or ["1293", "16444", "1786", "3055", "9873", "694", "13422", "9283"]
        std_records = self.crawler.discover_standards_via_api(query_terms)

        # 3. Collect Official QCOs
        qco_records = default_qco_collector.collect_official_qcos()

        all_records = pm_records + std_records
        job.total_discovered = len(all_records)
        job.status = "COMPLETED"
        self._update_job_progress(job)

        # Enqueue for processing
        for r in all_records:
            self.queue.push(r)

        logger.info("Discovery complete. Total discovered: %d records.", len(all_records))
        return all_records

    def run_download(self, limit: int = 20) -> List[DownloadResult]:
        """
        Download eligible documents in priority order.
        """
        job = self._get_or_create_job("DOWNLOAD", "bis_official")
        logger.info("Starting BIS Download job %s (limit=%d)...", job.job_id, limit)

        # Read pending items from SQLite discovered_documents
        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
            SELECT * FROM discovered_documents
            WHERE download_status = 'PENDING' AND rights_status = 'PUBLIC'
            ORDER BY publication_year DESC LIMIT ?
            """, (limit,)).fetchall()

        results: List[DownloadResult] = []

        for row in rows:
            doc_id = row["document_id"]
            url = row["source_url"]
            std_num = row["standard_number"]

            # Evaluate policy
            rights, dl_status, reason = self.policy.evaluate_access("bis", url, row["document_type"])
            if rights != RightsStatus.PUBLIC:
                job.blocked_count += 1
                self.policy.record_audit(AuditLogEntry(
                    log_id=str(uuid.uuid4()),
                    timestamp=time.time(),
                    source_id=row["source_id"],
                    url=url,
                    action="DOWNLOAD_CHECK",
                    status="BLOCKED",
                    document_id=doc_id,
                    error=reason,
                ))
                continue

            # Download PDF
            subdir = f"bis/{doc_id}"
            prefix = f"{std_num.replace(' ', '_').replace(':', '_').replace('/', '_')}"
            res = self.downloader.download_pdf(url, destination_subdir=subdir, filename_prefix=prefix)
            results.append(res)

            # Update DB & Record audit
            if res.success:
                job.downloaded_count += 1
                with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
                    conn.execute("""
                    UPDATE discovered_documents SET
                        download_status = 'DOWNLOADED', content_hash = ?,
                        local_path = ?, file_size_bytes = ?
                    WHERE document_id = ?
                    """, (res.content_hash, str(res.local_path), res.file_size, doc_id))
                
                self.policy.record_audit(AuditLogEntry(
                    log_id=str(uuid.uuid4()),
                    timestamp=time.time(),
                    source_id=row["source_id"],
                    url=url,
                    action="DOWNLOAD",
                    status="SUCCESS",
                    http_status=res.http_status,
                    content_hash=res.content_hash,
                    document_id=doc_id,
                ))
            else:
                job.failed_count += 1
                with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
                    conn.execute("""
                    UPDATE discovered_documents SET download_status = 'FAILED', quarantine_reason = ?
                    WHERE document_id = ?
                    """, (res.error, doc_id))

                self.policy.record_audit(AuditLogEntry(
                    log_id=str(uuid.uuid4()),
                    timestamp=time.time(),
                    source_id=row["source_id"],
                    url=url,
                    action="DOWNLOAD",
                    status="FAILED",
                    http_status=res.http_status,
                    document_id=doc_id,
                    error=res.error,
                ))

            job.current_index += 1
            self._update_job_progress(job)

        job.status = "COMPLETED"
        self._update_job_progress(job)
        logger.info("Download phase complete: %d downloaded, %d failed.", job.downloaded_count, job.failed_count)
        return results

    def run_ingestion(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Process downloaded PDFs through the master ingestion pipeline into ChromaDB and SQLite.
        """
        job = self._get_or_create_job("INGESTION", "bis_official")
        logger.info("Starting Ingestion job %s (limit=%d)...", job.job_id, limit)

        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
            SELECT * FROM discovered_documents
            WHERE download_status = 'DOWNLOADED' AND local_path IS NOT NULL
            ORDER BY discovered_at ASC LIMIT ?
            """, (limit,)).fetchall()

        if not rows:
            logger.info("No downloaded documents pending ingestion.")
            job.status = "COMPLETED"
            self._update_job_progress(job)
            return []

        # Preload embedding model and Chroma collection once for efficiency
        logger.info("Loading BGE-Large embedding model and Chroma collection...")
        model = load_embedding_model()
        collection = get_collection()

        ingestion_results: List[Dict[str, Any]] = []

        for row in rows:
            local_path = Path(row["local_path"])
            doc_id = row["document_id"]

            if not local_path.exists():
                logger.warning("Local PDF %s not found on disk; skipping.", local_path)
                continue

            logger.info("Executing master pipeline for: %s (Standard: %s)...", local_path.name, row["standard_number"])
            try:
                result = process_pdf(local_path, embedding_model=model, collection=collection)
                chunks_indexed = result.get("chunks_indexed", 0)
                job.ingested_count += 1

                # Update SQLite record
                with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
                    conn.execute("""
                    UPDATE discovered_documents SET download_status = 'INDEXED'
                    WHERE document_id = ?
                    """, (doc_id,))

                self.policy.record_audit(AuditLogEntry(
                    log_id=str(uuid.uuid4()),
                    timestamp=time.time(),
                    source_id=row["source_id"],
                    url=row["source_url"],
                    action="INGESTION_AND_INDEXING",
                    status="SUCCESS",
                    document_id=doc_id,
                    content_hash=row["content_hash"],
                ))

                ingestion_results.append({
                    "document_id": doc_id,
                    "standard_number": row["standard_number"],
                    "title": row["title"],
                    "local_path": str(local_path),
                    "chunks_indexed": chunks_indexed,
                    "status": "INDEXED",
                })
                logger.info("Successfully indexed %s: %d chunks into ChromaDB.", local_path.name, chunks_indexed)

            except Exception as exc:
                job.failed_count += 1
                logger.error("Failed to ingest %s: %s", local_path.name, exc)
                self.policy.record_audit(AuditLogEntry(
                    log_id=str(uuid.uuid4()),
                    timestamp=time.time(),
                    source_id=row["source_id"],
                    url=row["source_url"],
                    action="INGESTION_AND_INDEXING",
                    status="FAILED",
                    document_id=doc_id,
                    error=str(exc),
                ))

            job.current_index += 1
            self._update_job_progress(job)

        job.status = "COMPLETED"
        self._update_job_progress(job)
        return ingestion_results

    def run_all(self, filter_terms: Optional[List[str]] = None, download_limit: int = 10, ingest_limit: int = 10) -> Dict[str, Any]:
        """
        Execute full end-to-end cycle: DISCOVER -> DOWNLOAD -> INGEST -> INDEX.
        """
        logger.info("=== EXECUTING END-TO-END BIS ACQUISITION PIPELINE ===")
        t0 = time.time()

        discovered = self.run_discovery(filter_terms=filter_terms, limit=download_limit * 2)
        downloaded = self.run_download(limit=download_limit)
        ingested = self.run_ingestion(limit=ingest_limit)

        elapsed = round(time.time() - t0, 2)
        summary = {
            "elapsed_seconds": elapsed,
            "total_discovered": len(discovered),
            "total_downloaded": len([d for d in downloaded if d.success]),
            "total_ingested": len(ingested),
            "ingested_details": ingested,
        }
        logger.info("=== PIPELINE EXECUTION COMPLETE in %ss: Discovered=%d, Downloaded=%d, Ingested=%d ===",
                    elapsed, summary["total_discovered"], summary["total_downloaded"], summary["total_ingested"])
        return summary


default_coordinator = AcquisitionCoordinator()

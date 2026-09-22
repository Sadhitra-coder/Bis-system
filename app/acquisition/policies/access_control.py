"""app/acquisition/policies/access_control.py

Strict Rights, Access Control, and Licensing Policy Engine.

Enforces:
- PUBLIC: Gazette notifications, statutory QCOs, product manuals, guidelines -> Full-text allowed
- AUTHORIZED: Internally authenticated / authorized regulatory material -> Full-text allowed
- LOGIN_REQUIRED: Portal requires user/pass or API key -> BLOCKED_ACCESS (never bypass)
- LICENSE_REQUIRED: Proprietary standard full-text (BIS copyright) -> BLOCKED_ACCESS (metadata only)
- UNKNOWN: Unverified external domain -> BLOCKED_ACCESS (quarantine)

Maintains full audit trail in SQLite acquisition_audit_log table.
"""

import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple

from app.acquisition.models import AuditLogEntry, DownloadStatus, RightsStatus
from app.config import DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = DATA_DIR / "knowledge" / "bis_knowledge.db"


class AccessControlPolicy:
    """Gatekeeper enforcing intellectual property boundaries and regulatory compliance access rules."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._init_tables()

    def _init_tables(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS acquisition_audit_log (
                log_id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                source_id TEXT NOT NULL,
                url TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                http_status INTEGER,
                content_hash TEXT,
                document_id TEXT,
                error TEXT
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_time ON acquisition_audit_log(timestamp);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_doc ON acquisition_audit_log(document_id);")

    def record_audit(self, entry: AuditLogEntry) -> None:
        """Write audit entry to SQLite."""
        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            conn.execute("""
            INSERT INTO acquisition_audit_log (
                log_id, timestamp, source_id, url, action,
                status, http_status, content_hash, document_id, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.log_id or str(uuid.uuid4()),
                entry.timestamp or time.time(),
                entry.source_id,
                entry.url,
                entry.action,
                entry.status,
                entry.http_status,
                entry.content_hash,
                entry.document_id,
                entry.error,
            ))

    def evaluate_access(
        self,
        source_id: str,
        source_url: str,
        document_type: str,
    ) -> Tuple[RightsStatus, DownloadStatus, str]:
        """
        Evaluate legal and technical access permissions for a document.

        Returns:
            Tuple of (RightsStatus, DownloadStatus, reason)
        """
        lower_url = source_url.lower()

        # 1. Product Manuals and Guidelines on bis.gov.in
        if "bis.gov.in" in lower_url and any(k in lower_url for k in ["product-manual", "pm_", "guideline", "cart", "uploads"]):
            return (
                RightsStatus.PUBLIC,
                DownloadStatus.PENDING,
                "Official public product manual / certification guidelines. Full-text permitted.",
            )

        # 2. Gazette Notifications & QCOs
        if "egazette.gov.in" in lower_url or "dpiit.gov.in" in lower_url or document_type in ("QCO", "GAZETTE_NOTIFICATION"):
            return (
                RightsStatus.PUBLIC,
                DownloadStatus.PENDING,
                "Official statutory Gazette / QCO order. Public domain reproduction permitted.",
            )

        # 3. Standards Portal Specifications (Copyrighted)
        if "standardsbis.bsbedge.com" in lower_url or document_type in ("STANDARD", "STANDARD_SPECIFICATION"):
            return (
                RightsStatus.LICENSE_REQUIRED,
                DownloadStatus.BLOCKED_ACCESS,
                "Proprietary standard specification under BIS copyright. Full-text blocked; metadata indexing permitted.",
            )

        # 4. Login-protected e-BIS portals
        if "login" in lower_url or "standards.bis.gov.in/login" in lower_url:
            return (
                RightsStatus.LOGIN_REQUIRED,
                DownloadStatus.BLOCKED_ACCESS,
                "Authentication required. Automated bypass disallowed.",
            )

        # 5. Unknown / Unverified Source
        return (
            RightsStatus.UNKNOWN,
            DownloadStatus.BLOCKED_ACCESS,
            "Unverified source domain. Quarantined pending rights clearance.",
        )


default_access_policy = AccessControlPolicy()

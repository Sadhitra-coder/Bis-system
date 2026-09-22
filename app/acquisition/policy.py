"""app/acquisition/policy.py

Document Rights, Licensing Policy, and Access Control Enforcement.

Rule: Never automatically ingest, reproduce, or index the full text of
material whose rights are unclear or legally restricted.
"""

import hashlib
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional, Tuple

from app.acquisition.models import (
    DocumentPolicyRecord,
    LicenseStatus,
)
from app.config import DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = DATA_DIR / "knowledge" / "bis_knowledge.db"


class DocumentPolicyEnforcer:
    """
    Enforces intellectual property and rights boundaries for all incoming documents.
    Prevents unauthorized copying or indexing of proprietary standards.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()
        self._seed_baseline_rights()

    def _init_tables(self) -> None:
        with self._conn:
            self._conn.execute("""
            CREATE TABLE IF NOT EXISTS document_rights_ledger (
                document_id TEXT PRIMARY KEY,
                source_url TEXT NOT NULL,
                access_date TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                document_version TEXT NOT NULL,
                license_status TEXT NOT NULL,
                can_reproduce_full_text INTEGER NOT NULL,
                can_index_metadata INTEGER NOT NULL,
                remediation_notes TEXT,
                created_at REAL NOT NULL
            );
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_rights_status ON document_rights_ledger(license_status);")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_rights_hash ON document_rights_ledger(content_hash);")

    def _seed_baseline_rights(self) -> None:
        """Pre-populate rights classifications for known baseline regulatory documents."""
        now = time.time()
        baseline_docs = [
            (
                "doc_385",
                "https://egazette.gov.in/WriteReadData/2026/385.pdf",
                "2026-09-22T00:00:00Z",
                "4c5a98d3637e7228807da061eec4fef8e76c1beceb16260ab9099baaa77894a4",
                "1.0",
                LicenseStatus.PUBLIC.value,
                1,
                1,
                "Official Government Gazette publication. Full-text public domain reproduction permitted.",
            ),
            (
                "IS 1417:2016",
                "https://standardsbis.bsbedge.com/is1417",
                "2026-09-22T00:00:00Z",
                "hash_is_1417_metadata",
                "Fourth Revision",
                LicenseStatus.LICENSED.value,
                0,
                1,
                "Copyright BIS. Metadata, clauses, and titles indexed; full-text reproduction restricted.",
            ),
            (
                "IS 3055:2024",
                "https://standardsbis.bsbedge.com/is3055",
                "2026-09-22T00:00:00Z",
                "hash_is_3055_metadata",
                "1.0",
                LicenseStatus.LICENSED.value,
                0,
                1,
                "Copyright BIS. Metadata, clauses, and titles indexed; full-text reproduction restricted.",
            ),
            (
                "IS 16444:2015",
                "https://standardsbis.bsbedge.com/is16444",
                "2026-09-22T00:00:00Z",
                "hash_is_16444_metadata",
                "1.0",
                LicenseStatus.LICENSED.value,
                0,
                1,
                "Copyright BIS. Metadata, clauses, and titles indexed; full-text reproduction restricted.",
            ),
            (
                "IS 1293:2019",
                "https://standardsbis.bsbedge.com/is1293",
                "2026-09-22T00:00:00Z",
                "hash_is_1293_metadata",
                "Fifth Revision",
                LicenseStatus.LICENSED.value,
                0,
                1,
                "Copyright BIS. Metadata, clauses, and titles indexed; full-text reproduction restricted.",
            ),
            (
                "IS 15885 (Part 2/Sec 13):2012",
                "https://standardsbis.bsbedge.com/is15885",
                "2026-09-22T00:00:00Z",
                "hash_is_15885_metadata",
                "1.0",
                LicenseStatus.LICENSED.value,
                0,
                1,
                "Copyright BIS. Metadata, clauses, and titles indexed; full-text reproduction restricted.",
            ),
            (
                "IS 1786:2008",
                "https://standardsbis.bsbedge.com/is1786",
                "2026-09-22T00:00:00Z",
                "hash_is_1786_metadata",
                "Fourth Revision",
                LicenseStatus.LICENSED.value,
                0,
                1,
                "Copyright BIS. Metadata, clauses, and titles indexed; full-text reproduction restricted.",
            ),
            (
                "IS 9873 (Part 1):2019",
                "https://standardsbis.bsbedge.com/is9873",
                "2026-09-22T00:00:00Z",
                "hash_is_9873_metadata",
                "1.0",
                LicenseStatus.LICENSED.value,
                0,
                1,
                "Copyright BIS. Metadata, clauses, and titles indexed; full-text reproduction restricted.",
            ),
        ]
        with self._conn:
            for doc in baseline_docs:
                self._conn.execute("""
                INSERT OR IGNORE INTO document_rights_ledger (
                    document_id, source_url, access_date, content_hash,
                    document_version, license_status, can_reproduce_full_text,
                    can_index_metadata, remediation_notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (*doc, now))

    def evaluate_rights(
        self,
        source_domain: str,
        document_class: str,
        raw_bytes: Optional[bytes] = None,
        source_url: str = "",
        document_version: str = "1.0",
        document_id: Optional[str] = None
    ) -> Tuple[LicenseStatus, bool, bool]:
        """
        Classify incoming document rights and determine permitted operations.

        Returns:
            Tuple of (LicenseStatus, can_reproduce_full_text, can_index_metadata)
        """
        # 1. Statutory Gazette notifications & QCOs from egazette / ministries are public domain
        if "egazette.gov.in" in source_domain or document_class in ("QCO", "QCO_AMENDMENT", "GAZETTE_NOTIFICATION"):
            status = LicenseStatus.PUBLIC
            can_reproduce = True
            can_index_meta = True

        # 2. Public product certification manuals & testing schemes from manakonline
        elif "manakonline.in" in source_domain and document_class in ("PRODUCT_MANUAL", "TESTING_SCHEME", "CERTIFICATION_GUIDANCE", "LABORATORY_DIRECTORY"):
            status = LicenseStatus.PUBLIC
            can_reproduce = True
            can_index_meta = True

        # 3. BIS Standards full-text specs are copyrighted under Indian Copyright Act
        elif "standardsbis.bsbedge.com" in source_domain or document_class in ("STANDARD", "AMENDMENT"):
            status = LicenseStatus.LICENSED
            can_reproduce = False  # DO NOT scrape or reproduce full text without active license
            can_index_meta = True  # Metadata, titles, scopes, and committee info can be indexed

        # 4. Unknown sources require quarantine
        else:
            status = LicenseStatus.UNKNOWN_RIGHTS
            can_reproduce = False
            can_index_meta = False

        # If bytes provided, record in ledger
        if raw_bytes and document_id:
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            access_date = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            record = DocumentPolicyRecord(
                document_id=document_id,
                source_url=source_url,
                access_date=access_date,
                content_hash=content_hash,
                document_version=document_version,
                license_status=status,
                can_reproduce_full_text=can_reproduce,
                can_index_metadata=can_index_meta,
                remediation_notes=None if can_reproduce else f"Full-text reproduction restricted under {status.value} policy.",
            )
            self.record_policy_decision(record)

        return status, can_reproduce, can_index_meta

    def record_policy_decision(self, record: DocumentPolicyRecord) -> None:
        with self._conn:
            self._conn.execute("""
            INSERT OR REPLACE INTO document_rights_ledger (
                document_id, source_url, access_date, content_hash,
                document_version, license_status, can_reproduce_full_text,
                can_index_metadata, remediation_notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.document_id,
                record.source_url,
                record.access_date,
                record.content_hash,
                record.document_version,
                record.license_status.value,
                1 if record.can_reproduce_full_text else 0,
                1 if record.can_index_metadata else 0,
                record.remediation_notes,
                time.time()
            ))

    def get_policy_record(self, document_id: str) -> Optional[DocumentPolicyRecord]:
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM document_rights_ledger WHERE document_id = ?", (document_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return DocumentPolicyRecord(
            document_id=row["document_id"],
            source_url=row["source_url"],
            access_date=row["access_date"],
            content_hash=row["content_hash"],
            document_version=row["document_version"],
            license_status=LicenseStatus(row["license_status"]),
            can_reproduce_full_text=bool(row["can_reproduce_full_text"]),
            can_index_metadata=bool(row["can_index_metadata"]),
            remediation_notes=row["remediation_notes"],
        )


default_policy_enforcer = DocumentPolicyEnforcer()

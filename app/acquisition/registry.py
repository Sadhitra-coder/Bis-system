"""app/acquisition/registry.py

Source Registry for Scalable Government & Regulatory Knowledge Acquisition.
Maintains persistent records of authorized regulatory bodies, portals, and crawl configurations.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

from app.acquisition.models import (
    AuthorityLevel,
    CrawlFrequency,
    DocumentClass,
    JurisdictionCode,
    LicenseStatus,
    SourceRegistryRecord,
    SourceType,
)
from app.config import DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_ACQUISITION_DB = DATA_DIR / "knowledge" / "bis_knowledge.db"


class SourceRegistry:
    """
    Thread-safe registry for official regulatory portals and crawlers.
    Stored in SQLite to guarantee ACID transactions and auditability.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_ACQUISITION_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()
        self._seed_default_sources()

    def _init_tables(self) -> None:
        with self._conn:
            self._conn.execute("""
            CREATE TABLE IF NOT EXISTS source_registry (
                source_id TEXT PRIMARY KEY,
                source_organization TEXT NOT NULL,
                source_domain TEXT NOT NULL,
                source_type TEXT NOT NULL,
                authority_level TEXT NOT NULL,
                jurisdiction TEXT NOT NULL,
                allowed_document_classes TEXT NOT NULL,
                crawl_frequency TEXT NOT NULL,
                parser TEXT NOT NULL,
                license_access_policy TEXT NOT NULL,
                source_url TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                last_successful_crawl TEXT,
                last_change TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_source_jurisdiction ON source_registry(jurisdiction);")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_source_active ON source_registry(is_active);")

    def _seed_default_sources(self) -> None:
        """Seed baseline authorized Indian regulatory and BIS bodies."""
        now = time.time()
        baseline_sources = [
            SourceRegistryRecord(
                source_id="bis_standards_portal",
                source_organization="Bureau of Indian Standards",
                source_domain="standardsbis.bsbedge.com",
                source_type=SourceType.STANDARDS_BODY,
                authority_level=AuthorityLevel.STATUTORY_NATIONAL,
                jurisdiction=JurisdictionCode.INDIA_NATIONAL,
                allowed_document_classes=[
                    DocumentClass.STANDARD,
                    DocumentClass.AMENDMENT,
                    DocumentClass.CERTIFICATION_GUIDANCE,
                ],
                crawl_frequency=CrawlFrequency.WEEKLY,
                parser="bis_standard_parser",
                license_access_policy=LicenseStatus.LICENSED,
                source_url="https://standardsbis.bsbedge.com",
                is_active=True,
                created_at=now,
                updated_at=now,
            ),
            SourceRegistryRecord(
                source_id="bis_manakonline",
                source_organization="Bureau of Indian Standards - Manakonline",
                source_domain="manakonline.in",
                source_type=SourceType.OFFICIAL_PORTAL,
                authority_level=AuthorityLevel.STATUTORY_NATIONAL,
                jurisdiction=JurisdictionCode.INDIA_NATIONAL,
                allowed_document_classes=[
                    DocumentClass.PRODUCT_MANUAL,
                    DocumentClass.TESTING_SCHEME,
                    DocumentClass.CERTIFICATION_GUIDANCE,
                    DocumentClass.LABORATORY_DIRECTORY,
                ],
                crawl_frequency=CrawlFrequency.DAILY,
                parser="bis_manual_parser",
                license_access_policy=LicenseStatus.PUBLIC,
                source_url="https://www.manakonline.in",
                is_active=True,
                created_at=now,
                updated_at=now,
            ),
            SourceRegistryRecord(
                source_id="egazette_india",
                source_organization="Government of India - Directorate of Printing",
                source_domain="egazette.gov.in",
                source_type=SourceType.GAZETTE,
                authority_level=AuthorityLevel.CENTRAL_MINISTRY,
                jurisdiction=JurisdictionCode.INDIA_NATIONAL,
                allowed_document_classes=[
                    DocumentClass.GAZETTE_NOTIFICATION,
                    DocumentClass.QCO,
                    DocumentClass.QCO_AMENDMENT,
                ],
                crawl_frequency=CrawlFrequency.DAILY,
                parser="gazette_parser",
                license_access_policy=LicenseStatus.PUBLIC,
                source_url="https://egazette.gov.in",
                is_active=True,
                created_at=now,
                updated_at=now,
            ),
            SourceRegistryRecord(
                source_id="dpiit_qco_portal",
                source_organization="Department for Promotion of Industry and Internal Trade (DPIIT)",
                source_domain="dpiit.gov.in",
                source_type=SourceType.MINISTRY_PORTAL,
                authority_level=AuthorityLevel.CENTRAL_MINISTRY,
                jurisdiction=JurisdictionCode.INDIA_NATIONAL,
                allowed_document_classes=[
                    DocumentClass.QCO,
                    DocumentClass.QCO_AMENDMENT,
                ],
                crawl_frequency=CrawlFrequency.WEEKLY,
                parser="qco_parser",
                license_access_policy=LicenseStatus.PUBLIC,
                source_url="https://dpiit.gov.in/quality-control-orders",
                is_active=True,
                created_at=now,
                updated_at=now,
            ),
            SourceRegistryRecord(
                source_id="mca_consumeraffairs",
                source_organization="Ministry of Consumer Affairs, Food and Public Distribution",
                source_domain="consumeraffairs.nic.in",
                source_type=SourceType.MINISTRY_PORTAL,
                authority_level=AuthorityLevel.CENTRAL_MINISTRY,
                jurisdiction=JurisdictionCode.INDIA_NATIONAL,
                allowed_document_classes=[
                    DocumentClass.QCO,
                    DocumentClass.GAZETTE_NOTIFICATION,
                    DocumentClass.CERTIFICATION_GUIDANCE,
                ],
                crawl_frequency=CrawlFrequency.WEEKLY,
                parser="gazette_parser",
                license_access_policy=LicenseStatus.PUBLIC,
                source_url="https://consumeraffairs.nic.in",
                is_active=True,
                created_at=now,
                updated_at=now,
            ),
            SourceRegistryRecord(
                source_id="meity_qco_portal",
                source_organization="Ministry of Electronics and Information Technology (MeitY)",
                source_domain="meity.gov.in",
                source_type=SourceType.MINISTRY_PORTAL,
                authority_level=AuthorityLevel.CENTRAL_MINISTRY,
                jurisdiction=JurisdictionCode.INDIA_NATIONAL,
                allowed_document_classes=[
                    DocumentClass.QCO,
                    DocumentClass.CERTIFICATION_GUIDANCE,
                ],
                crawl_frequency=CrawlFrequency.WEEKLY,
                parser="qco_parser",
                license_access_policy=LicenseStatus.PUBLIC,
                source_url="https://www.meity.gov.in/esdm/standards",
                is_active=True,
                created_at=now,
                updated_at=now,
            ),
            # Extensible International Source (Design for later UK expansion)
            SourceRegistryRecord(
                source_id="bsi_standards_uk",
                source_organization="British Standards Institution (BSI)",
                source_domain="bsigroup.com",
                source_type=SourceType.STANDARDS_BODY,
                authority_level=AuthorityLevel.STATUTORY_NATIONAL,
                jurisdiction=JurisdictionCode.UK,
                allowed_document_classes=[DocumentClass.STANDARD],
                crawl_frequency=CrawlFrequency.MONTHLY,
                parser="bsi_parser",
                license_access_policy=LicenseStatus.LICENSED,
                source_url="https://www.bsigroup.com",
                is_active=False,  # Inactive by default until UK expansion activated
                created_at=now,
                updated_at=now,
            ),
        ]

        for s in baseline_sources:
            if not self.get_source(s.source_id):
                self.register_source(s)

    def register_source(self, record: SourceRegistryRecord) -> None:
        """Insert or update a source record."""
        now = time.time()
        classes_json = json.dumps([c.value for c in record.allowed_document_classes])
        with self._conn:
            self._conn.execute("""
            INSERT OR REPLACE INTO source_registry (
                source_id, source_organization, source_domain, source_type,
                authority_level, jurisdiction, allowed_document_classes,
                crawl_frequency, parser, license_access_policy, source_url,
                is_active, last_successful_crawl, last_change, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.source_id,
                record.source_organization,
                record.source_domain,
                record.source_type.value,
                record.authority_level.value,
                record.jurisdiction.value,
                classes_json,
                record.crawl_frequency.value,
                record.parser,
                record.license_access_policy.value,
                record.source_url,
                1 if record.is_active else 0,
                record.last_successful_crawl,
                record.last_change,
                record.created_at or now,
                now,
            ))

    def get_source(self, source_id: str) -> Optional[SourceRegistryRecord]:
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM source_registry WHERE source_id = ?", (source_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def list_sources(self, jurisdiction: Optional[str] = None, active_only: bool = True) -> List[SourceRegistryRecord]:
        query = "SELECT * FROM source_registry WHERE 1=1"
        params = []
        if active_only:
            query += " AND is_active = 1"
        if jurisdiction:
            query += " AND jurisdiction LIKE ?"
            params.append(f"{jurisdiction}%")
        query += " ORDER BY source_organization"

        cursor = self._conn.cursor()
        cursor.execute(query, params)
        return [self._row_to_record(row) for row in cursor.fetchall()]

    def get_sources_by_authority(self, authority: AuthorityLevel, active_only: bool = False) -> List[SourceRegistryRecord]:
        """Filter registered sources by statutory authority level."""
        return [s for s in self.list_sources(active_only=active_only) if s.authority_level == authority]

    def get_sources_by_frequency(self, frequency: CrawlFrequency, active_only: bool = False) -> List[SourceRegistryRecord]:
        """Filter registered sources by scheduled crawl frequency."""
        return [s for s in self.list_sources(active_only=active_only) if s.crawl_frequency == frequency]

    def update_crawl_timestamp(self, source_id: str, success: bool, last_change: Optional[str] = None) -> None:
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._conn:
            if success:
                self._conn.execute(
                    "UPDATE source_registry SET last_successful_crawl = ?, last_change = COALESCE(?, last_change), updated_at = ? WHERE source_id = ?",
                    (now_iso, last_change, time.time(), source_id)
                )
            else:
                self._conn.execute(
                    "UPDATE source_registry SET updated_at = ? WHERE source_id = ?",
                    (time.time(), source_id)
                )

    def _row_to_record(self, row: sqlite3.Row) -> SourceRegistryRecord:
        allowed = [DocumentClass(c) for c in json.loads(row["allowed_document_classes"])]
        return SourceRegistryRecord(
            source_id=row["source_id"],
            source_organization=row["source_organization"],
            source_domain=row["source_domain"],
            source_type=SourceType(row["source_type"]),
            authority_level=AuthorityLevel(row["authority_level"]),
            jurisdiction=JurisdictionCode(row["jurisdiction"]),
            allowed_document_classes=allowed,
            crawl_frequency=CrawlFrequency(row["crawl_frequency"]),
            parser=row["parser"],
            license_access_policy=LicenseStatus(row["license_access_policy"]),
            source_url=row["source_url"],
            is_active=bool(row["is_active"]),
            last_successful_crawl=row["last_successful_crawl"],
            last_change=row["last_change"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


default_source_registry = SourceRegistry()

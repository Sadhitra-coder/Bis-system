"""app/acquisition/discovery.py

Official-Source Discovery Engine and Metadata-First Ingestion Planner.

Gathers authoritative standards metadata, QCOs, amendments, and manuals
from official portals before any heavy full-text ingestion occurs.
"""

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.acquisition.models import (
    AcquisitionPriority,
    DiscoveredItem,
    DocumentClass,
    LicenseStatus,
    StandardMetadataRecord,
)
from app.acquisition.policy import default_policy_enforcer
from app.acquisition.registry import default_source_registry
from app.config import DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = DATA_DIR / "knowledge" / "bis_knowledge.db"


class DiscoveryEngine:
    """
    Scans and indexes regulatory compliance artifacts across official sources.
    Maintains the metadata-first catalog and prioritized acquisition queue.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()
        self._seed_baseline_catalog()

    def _init_tables(self) -> None:
        with self._conn:
            self._conn.execute("""
            CREATE TABLE IF NOT EXISTS discovered_items (
                item_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                document_class TEXT NOT NULL,
                title TEXT NOT NULL,
                identifier TEXT NOT NULL,
                standard_year INTEGER,
                edition_or_version TEXT,
                publication_date TEXT,
                effective_date TEXT,
                source_url TEXT NOT NULL,
                license_status TEXT NOT NULL,
                access_date TEXT NOT NULL,
                content_hash TEXT,
                priority INTEGER NOT NULL,
                metadata_payload TEXT NOT NULL,
                is_ingested INTEGER NOT NULL DEFAULT 0,
                ingestion_job_id TEXT,
                quarantine_reason TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_disc_identifier ON discovered_items(identifier);")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_disc_priority ON discovered_items(priority);")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_disc_ingested ON discovered_items(is_ingested);")

            self._conn.execute("""
            CREATE TABLE IF NOT EXISTS standards_metadata_catalog (
                standard_number TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                year INTEGER,
                edition_or_version TEXT,
                status TEXT NOT NULL,
                category TEXT,
                technical_committee TEXT,
                ics_code TEXT,
                applicable_products TEXT NOT NULL,
                mandatory_qco_number TEXT,
                is_mandatory INTEGER NOT NULL DEFAULT 0,
                source_url TEXT NOT NULL,
                license_status TEXT NOT NULL,
                full_text_available INTEGER NOT NULL DEFAULT 0,
                document_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_category ON standards_metadata_catalog(category);")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_mandatory ON standards_metadata_catalog(is_mandatory);")

    def _seed_baseline_catalog(self) -> None:
        """Seed rich metadata for major Indian Standards and QCOs."""
        now = time.time()
        baseline_records = [
            # 1. Gold Jewellery Hallmarking QCO & Standard (Priority 1)
            StandardMetadataRecord(
                standard_number="IS 1417:2016",
                title="Gold and Gold Alloys, Jewellery/Artefacts - Fineness and Marking - Specification",
                year=2016,
                edition_or_version="Fourth Revision",
                status="CURRENT",
                category="Precious Metals & Hallmarking",
                technical_committee="MTD 10 - Precious Metals",
                ics_code="39.060",
                applicable_products=["Gold Jewellery", "Gold Artefacts", "Bullion"],
                mandatory_qco_number="S.O. 4345(E) / Third Amendment 2026",
                is_mandatory=True,
                source_url="https://standardsbis.bsbedge.com",
                license_status=LicenseStatus.LICENSED,
                full_text_available=False,
                created_at=now,
                updated_at=now,
            ),
            # 2. Clinical Thermometers (IS 3055) (Priority 1)
            StandardMetadataRecord(
                standard_number="IS 3055:2024",
                title="Clinical Thermometers - Specification - Part 1: Solid Stem Type",
                year=2024,
                edition_or_version="Third Edition",
                status="CURRENT",
                category="Medical Equipment",
                technical_committee="MHD 02 - Clinical Thermometers",
                ics_code="17.200.20",
                applicable_products=["Clinical Thermometers", "Fever Thermometers"],
                mandatory_qco_number="Medical Devices QCO 2020",
                is_mandatory=True,
                source_url="https://standardsbis.bsbedge.com",
                license_status=LicenseStatus.LICENSED,
                full_text_available=True,
                document_id="doc_90d46c947dc18a22",
                created_at=now,
                updated_at=now,
            ),
            # 3. Smart Meters (IS 16444) (Priority 1)
            StandardMetadataRecord(
                standard_number="IS 16444:2015",
                title="a.c. Static Direct Connected Watt-hour Smart Meter Class 1 and 2 - Specification",
                year=2015,
                edition_or_version="First Edition",
                status="CURRENT",
                category="Electrical & Electronics",
                technical_committee="ETD 13 - Electrical Appliances and Electricity Meters",
                ics_code="17.220.20",
                applicable_products=["Smart Electricity Meters", "Single Phase Static Meters", "Three Phase Static Meters"],
                mandatory_qco_number="Electrical Apparatus for Explosive Atmospheres and Meters QCO 2023",
                is_mandatory=True,
                source_url="https://standardsbis.bsbedge.com",
                license_status=LicenseStatus.LICENSED,
                full_text_available=False,
                created_at=now,
                updated_at=now,
            ),
            # 4. Plugs and Socket-Outlets (IS 1293) (Priority 1)
            StandardMetadataRecord(
                standard_number="IS 1293:2019",
                title="Plugs and Socket-Outlets of Rated Voltage up to and Including 250 V and Rated Current up to and Including 16 A",
                year=2019,
                edition_or_version="Third Revision",
                status="CURRENT",
                category="Electrical Accessories",
                technical_committee="ETD 14 - Electrical Accessories",
                ics_code="29.120.30",
                applicable_products=["Domestic Plugs", "Socket Outlets", "Multiway Adaptors"],
                mandatory_qco_number="Plugs and Socket-Outlets QCO 2021",
                is_mandatory=True,
                source_url="https://standardsbis.bsbedge.com",
                license_status=LicenseStatus.LICENSED,
                full_text_available=False,
                created_at=now,
                updated_at=now,
            ),
            # 5. LED Drivers & Lighting (IS 15885) (Priority 1)
            StandardMetadataRecord(
                standard_number="IS 15885 (Part 2/Sec 13):2012",
                title="Lamp Controlgear - Part 2: Particular Requirements - Section 13: d.c. or a.c. Supplied Electronic Controlgear for LED Modules",
                year=2012,
                edition_or_version="First Edition",
                status="CURRENT",
                category="Lighting & Electronics",
                technical_committee="ETD 23 - Electric Lamps and Luminaires",
                ics_code="29.140.99",
                applicable_products=["LED Power Supplies", "LED Drivers", "Electronic Controlgear"],
                mandatory_qco_number="Electronics and IT Goods (Requirement for Compulsory Registration) Order, 2021",
                is_mandatory=True,
                source_url="https://standardsbis.bsbedge.com",
                license_status=LicenseStatus.LICENSED,
                full_text_available=False,
                created_at=now,
                updated_at=now,
            ),
            # 6. Steel Bars for Concrete Reinforcement (IS 1786) (Priority 1)
            StandardMetadataRecord(
                standard_number="IS 1786:2008",
                title="High Strength Deformed Steel Bars and Wires for Concrete Reinforcement - Specification",
                year=2008,
                edition_or_version="Fourth Revision",
                status="CURRENT",
                category="Steel & Metallurgy",
                technical_committee="MTD 04 - Wrought Steel Products",
                ics_code="77.140.15",
                applicable_products=["TMT Steel Bars", "Thermo-Mechanically Treated Rebars"],
                mandatory_qco_number="Steel and Steel Products (Quality Control) Order 2020",
                is_mandatory=True,
                source_url="https://standardsbis.bsbedge.com",
                license_status=LicenseStatus.LICENSED,
                full_text_available=False,
                created_at=now,
                updated_at=now,
            ),
            # 7. Toys Safety (IS 9873) (Priority 1)
            StandardMetadataRecord(
                standard_number="IS 9873 (Part 1):2019",
                title="Safety of Toys - Part 1: Safety Aspects Related to Mechanical and Physical Properties",
                year=2019,
                edition_or_version="Third Revision",
                status="CURRENT",
                category="Consumer Products & Child Safety",
                technical_committee="PCD 12 - Toys Safety",
                ics_code="97.200.50",
                applicable_products=["Toys", "Educational Games", "Plush Toys"],
                mandatory_qco_number="Toys (Quality Control) Order, 2020",
                is_mandatory=True,
                source_url="https://standardsbis.bsbedge.com",
                license_status=LicenseStatus.LICENSED,
                full_text_available=False,
                created_at=now,
                updated_at=now,
            ),
        ]

        for r in baseline_records:
            if not self.get_standard_metadata(r.standard_number):
                self.record_standard_metadata(r)

    def record_standard_metadata(self, record: StandardMetadataRecord) -> None:
        now = time.time()
        products_json = json.dumps(record.applicable_products)
        with self._conn:
            self._conn.execute("""
            INSERT OR REPLACE INTO standards_metadata_catalog (
                standard_number, title, year, edition_or_version, status,
                category, technical_committee, ics_code, applicable_products,
                mandatory_qco_number, is_mandatory, source_url, license_status,
                full_text_available, document_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.standard_number,
                record.title,
                record.year,
                record.edition_or_version,
                record.status,
                record.category,
                record.technical_committee,
                record.ics_code,
                products_json,
                record.mandatory_qco_number,
                1 if record.is_mandatory else 0,
                record.source_url,
                record.license_status.value,
                1 if record.full_text_available else 0,
                record.document_id,
                record.created_at or now,
                now,
            ))

    def get_standard_metadata(self, standard_number: str) -> Optional[StandardMetadataRecord]:
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM standards_metadata_catalog WHERE standard_number = ?", (standard_number,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_metadata(row)

    def list_standards_metadata(
        self,
        category: Optional[str] = None,
        mandatory_only: bool = False,
        limit: int = 100
    ) -> List[StandardMetadataRecord]:
        query = "SELECT * FROM standards_metadata_catalog WHERE 1=1"
        params = []
        if mandatory_only:
            query += " AND is_mandatory = 1"
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY standard_number LIMIT ?"
        params.append(limit)

        cursor = self._conn.cursor()
        cursor.execute(query, params)
        return [self._row_to_metadata(row) for row in cursor.fetchall()]

    def list_mandatory_standards(self, limit: int = 100) -> List[StandardMetadataRecord]:
        """List all standards that are mandated by statutory Quality Control Orders (QCOs)."""
        return self.list_standards_metadata(mandatory_only=True, limit=limit)

    def get_standards_by_category(self, category: str, limit: int = 100) -> List[StandardMetadataRecord]:
        """Filter standards by industrial sector / category."""
        return self.list_standards_metadata(category=category, limit=limit)

    def record_discovered_item(self, item: DiscoveredItem) -> None:
        now = time.time()
        payload_json = json.dumps(item.metadata_payload)
        with self._conn:
            self._conn.execute("""
            INSERT OR REPLACE INTO discovered_items (
                item_id, source_id, document_class, title, identifier,
                standard_year, edition_or_version, publication_date, effective_date,
                source_url, license_status, access_date, content_hash,
                priority, metadata_payload, is_ingested, ingestion_job_id,
                quarantine_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.item_id,
                item.source_id,
                item.document_class.value,
                item.title,
                item.identifier,
                item.standard_year,
                item.edition_or_version,
                item.publication_date,
                item.effective_date,
                item.source_url,
                item.license_status.value,
                item.access_date,
                item.content_hash,
                item.priority.value,
                payload_json,
                1 if item.is_ingested else 0,
                item.ingestion_job_id,
                item.quarantine_reason,
                now,
                now,
            ))

    def get_pending_discoveries(self, max_priority: int = 4) -> List[DiscoveredItem]:
        cursor = self._conn.cursor()
        cursor.execute("""
        SELECT * FROM discovered_items
        WHERE is_ingested = 0 AND quarantine_reason IS NULL AND priority <= ?
        ORDER BY priority ASC, created_at ASC
        """, (max_priority,))
        return [self._row_to_discovery(row) for row in cursor.fetchall()]

    def _row_to_metadata(self, row: sqlite3.Row) -> StandardMetadataRecord:
        prods = json.loads(row["applicable_products"])
        return StandardMetadataRecord(
            standard_number=row["standard_number"],
            title=row["title"],
            year=row["year"],
            edition_or_version=row["edition_or_version"],
            status=row["status"],
            category=row["category"],
            technical_committee=row["technical_committee"],
            ics_code=row["ics_code"],
            applicable_products=prods,
            mandatory_qco_number=row["mandatory_qco_number"],
            is_mandatory=bool(row["is_mandatory"]),
            source_url=row["source_url"],
            license_status=LicenseStatus(row["license_status"]),
            full_text_available=bool(row["full_text_available"]),
            document_id=row["document_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_discovery(self, row: sqlite3.Row) -> DiscoveredItem:
        return DiscoveredItem(
            item_id=row["item_id"],
            source_id=row["source_id"],
            document_class=DocumentClass(row["document_class"]),
            title=row["title"],
            identifier=row["identifier"],
            standard_year=row["standard_year"],
            edition_or_version=row["edition_or_version"],
            publication_date=row["publication_date"],
            effective_date=row["effective_date"],
            source_url=row["source_url"],
            license_status=LicenseStatus(row["license_status"]),
            access_date=row["access_date"],
            content_hash=row["content_hash"],
            priority=AcquisitionPriority(row["priority"]),
            metadata_payload=json.loads(row["metadata_payload"]),
            is_ingested=bool(row["is_ingested"]),
            ingestion_job_id=row["ingestion_job_id"],
            quarantine_reason=row["quarantine_reason"],
        )


default_discovery_engine = DiscoveryEngine()

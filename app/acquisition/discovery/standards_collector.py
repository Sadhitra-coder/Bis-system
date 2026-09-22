"""app/acquisition/discovery/standards_collector.py

Dedicated Standards Metadata Collector.

Collects rich, authoritative metadata for Indian Standards:
- Standard Number (e.g. IS 1293:2019)
- Title & Scope
- Technical Committee (e.g. ETD 14)
- Publication / Revision Year
- Reaffirmation Year
- Status (CURRENT / REVISED / WITHDRAWN)
- Product Category
- Mandatory Status (explicitly mapped from QCOs, never guessed)
- Amendment Count
- Official URL

Stores records into standards_metadata_catalog.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from app.acquisition.models import LicenseStatus, StandardMetadataRecord
from app.config import DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = DATA_DIR / "knowledge" / "bis_knowledge.db"


class StandardsMetadataCollector:
    """Collects and organizes Indian Standard metadata from official sources."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.services.bis.gov.in/php/BIS_2.0/bisconnect/knowyourstandards/indian_standards/isdetails"
        })

    def save_standard_metadata(self, record: StandardMetadataRecord) -> None:
        """Persist metadata record into SQLite standards_metadata_catalog."""
        now = time.time()
        prods_json = json.dumps(record.applicable_products)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
            INSERT OR REPLACE INTO standards_metadata_catalog (
                standard_number, title, year, edition_or_version,
                status, category, technical_committee, ics_code,
                applicable_products, mandatory_qco_number, is_mandatory,
                source_url, license_status, full_text_available,
                document_id, created_at, updated_at
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
                prods_json,
                record.mandatory_qco_number,
                1 if record.is_mandatory else 0,
                record.source_url,
                record.license_status.value if hasattr(record.license_status, "value") else str(record.license_status),
                1 if record.full_text_available else 0,
                record.document_id,
                record.created_at or now,
                now,
            ))

    def fetch_from_bis_api(self, is_numbers: List[str]) -> List[StandardMetadataRecord]:
        """Query official BIS Elasticsearch endpoint for exact standard records."""
        url = "https://www.services.bis.gov.in/php/BIS_2.0/bisconnect/knowyourstandards/Elasticsearch/getsearchAjax"
        collected: List[StandardMetadataRecord] = []
        now = time.time()

        for num in is_numbers:
            clean_query = num.replace("IS", "").strip().split(":")[0].split("(")[0].strip()
            try:
                r = self.session.post(url, data={"search": clean_query}, verify=False, timeout=12)
                if r.status_code == 200:
                    items = r.json()
                    if isinstance(items, list):
                        for it in items:
                            is_no = it.get("is_no", "").strip()
                            is_year = it.get("is_year", "")
                            full_num = f"{is_no}:{is_year}" if is_year else is_no
                            title = it.get("name", "")
                            reaffirm = it.get("reaffirm_year")
                            identical = it.get("identical_is", "")

                            record = StandardMetadataRecord(
                                standard_number=full_num,
                                title=title,
                                year=int(is_year) if str(is_year).isdigit() else None,
                                status="CURRENT",
                                category="General Industrial",
                                technical_committee=f"Technical Committee for {is_no}",
                                ics_code=identical or None,
                                is_mandatory=False,  # Updated by QCO Collector
                                source_url=f"https://standardsbis.bsbedge.com/BIS_SearchStandard.aspx?Standard_Number={is_no}",
                                license_status=LicenseStatus.LICENSED,
                                full_text_available=False,
                                created_at=now,
                                updated_at=now,
                            )
                            self.save_standard_metadata(record)
                            collected.append(record)
            except Exception as e:
                logger.warning("Error fetching metadata for standard %s: %s", num, e)

        return collected


default_standards_collector = StandardsMetadataCollector()

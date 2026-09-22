"""app/acquisition/sources/registry_loader.py

Loads and synchronizes the machine-readable official source registry (data/sources/bis_sources.yaml)
into the persistent SQLite source_registry table.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

from app.acquisition.models import (
    AuthorityLevel,
    CrawlFrequency,
    DocumentClass,
    JurisdictionCode,
    LicenseStatus,
    SourceRegistryRecord,
    SourceType,
)
from app.acquisition.registry import SourceRegistry, default_source_registry
from app.config import DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_YAML_PATH = DATA_DIR / "sources" / "bis_sources.yaml"


class SourceRegistryLoader:
    """Manages loading and syncing of the YAML source registry into SQLite."""

    def __init__(self, yaml_path: Optional[Path] = None, registry: Optional[SourceRegistry] = None):
        self.yaml_path = Path(yaml_path) if yaml_path else DEFAULT_YAML_PATH
        self.registry = registry or default_source_registry

    def load_from_yaml(self) -> List[SourceRegistryRecord]:
        """Parse bis_sources.yaml into typed SourceRegistryRecord models."""
        if not self.yaml_path.exists():
            logger.warning("Source YAML not found at: %s", self.yaml_path)
            return []

        with open(self.yaml_path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f)

        records: List[SourceRegistryRecord] = []
        raw_sources = raw_data.get("sources", []) if isinstance(raw_data, dict) else []

        for item in raw_sources:
            try:
                # Map document classes
                doc_classes = []
                for dc in item.get("document_types", []):
                    try:
                        doc_classes.append(DocumentClass(dc))
                    except ValueError:
                        pass
                if not doc_classes:
                    doc_classes = [DocumentClass.STANDARD]

                # Map source type
                stype = SourceType.OFFICIAL_PORTAL
                try:
                    stype = SourceType(item.get("source_type", ""))
                except ValueError:
                    pass

                # Map authority
                auth = AuthorityLevel.STATUTORY_NATIONAL
                try:
                    auth = AuthorityLevel(item.get("authority_level", ""))
                except ValueError:
                    pass

                # Map jurisdiction
                jur = JurisdictionCode.INDIA_NATIONAL
                try:
                    jur = JurisdictionCode(item.get("jurisdiction", ""))
                except ValueError:
                    pass

                # Map frequency
                freq = CrawlFrequency.WEEKLY
                try:
                    freq = CrawlFrequency(item.get("crawl_frequency", ""))
                except ValueError:
                    pass

                # Map policy
                policy = LicenseStatus.PUBLIC
                try:
                    policy = LicenseStatus(item.get("rights_policy", ""))
                except ValueError:
                    pass

                rec = SourceRegistryRecord(
                    source_id=item["source_id"],
                    source_organization=item["organization"],
                    source_domain=Path(item["canonical_url"]).netloc if hasattr(Path(item["canonical_url"]), "netloc") else item["canonical_url"].replace("https://", "").replace("http://", "").split("/")[0],
                    source_type=stype,
                    authority_level=auth,
                    jurisdiction=jur,
                    allowed_document_classes=doc_classes,
                    crawl_frequency=freq,
                    parser=item.get("discovery_method", "generic_parser"),
                    license_access_policy=policy,
                    source_url=item["canonical_url"],
                    is_active=bool(item.get("enabled", True)),
                )
                records.append(rec)
            except Exception as e:
                logger.error("Error parsing source entry %s: %s", item.get("source_id"), e)

        return records

    def sync_to_sqlite(self) -> int:
        """Synchronize all YAML sources into the SQLite persistent source registry."""
        records = self.load_from_yaml()
        count = 0
        for r in records:
            self.registry.register_source(r)
            count += 1
        logger.info("Synchronized %d official sources from YAML into SQLite.", count)
        return count


default_registry_loader = SourceRegistryLoader()

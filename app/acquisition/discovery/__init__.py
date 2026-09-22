"""app/acquisition/discovery/__init__.py

Discovery Subsystem for Official Indian Compliance Documents.
"""

from app.acquisition.discovery.crawler import BISOfficialCrawler, default_bis_crawler
from app.acquisition.discovery.qco_collector import QCOCollector, default_qco_collector
from app.acquisition.discovery.standards_collector import StandardsMetadataCollector, default_standards_collector
from app.acquisition.discovery.engine import DiscoveryEngine, default_discovery_engine

__all__ = [
    "BISOfficialCrawler",
    "default_bis_crawler",
    "QCOCollector",
    "default_qco_collector",
    "StandardsMetadataCollector",
    "default_standards_collector",
    "DiscoveryEngine",
    "default_discovery_engine",
]

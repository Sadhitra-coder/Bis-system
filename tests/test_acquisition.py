"""tests/test_acquisition.py

Comprehensive Test Suite for the Scalable Knowledge Acquisition and Update Engine:
1. Source Registry (Multi-tier authority, crawl frequency, filtering)
2. Document Rights & Licensing Policy (Strict intellectual property enforcement)
3. Priority Queue (Priority 1 QCOs down to Priority 4 Voluntary standards)
4. Discovery Engine (Metadata-first catalog, mandatory flags, sector breakdown)
5. Knowledge Graph (QCO, Scheme, Product, Authority, Jurisdiction relationships)
6. Update Engine & Deduplication (Content-hash change detection, quality gating)
7. Coverage Reporter (Live metrics aggregation, audit markdown generation)
"""

import hashlib
import json
import pytest
from pathlib import Path

from app.acquisition.models import (
    AcquisitionPriority,
    AuthorityLevel,
    CrawlFrequency,
    DiscoveredItem,
    DocumentClass,
    JurisdictionCode,
    JurisdictionLevel,
    LicenseStatus,
    SourceRegistryRecord,
    SourceType,
    StandardMetadataRecord,
)
from app.acquisition.registry import SourceRegistry, default_source_registry
from app.acquisition.policy import DocumentPolicyEnforcer, default_policy_enforcer
from app.acquisition.priority_queue import AcquisitionPriorityQueue
from app.acquisition.discovery import DiscoveryEngine, default_discovery_engine
from app.acquisition.update_engine import UpdateEngine, SyncEvaluationResult
from app.acquisition.coverage_reporter import CoverageReporter, default_coverage_reporter
from app.knowledge.repository import KnowledgeRepository, default_repository
from app.knowledge.models import RelationshipType


# ============================================================
# 1. SOURCE REGISTRY TESTS
# ============================================================

def test_source_registry_defaults():
    """Verify official sources are registered with proper authority tiers."""
    sources = default_source_registry.list_sources(active_only=False)
    assert len(sources) >= 7

    source_ids = {s.source_id for s in sources}
    assert "bis_standards_portal" in source_ids
    assert "bis_manakonline" in source_ids
    assert "egazette_india" in source_ids
    assert "dpiit_qco_portal" in source_ids
    assert "meity_qco_portal" in source_ids
    assert "mca_consumeraffairs" in source_ids
    assert "bsi_standards_uk" in source_ids


def test_source_registry_filtering():
    """Test filtering sources by authority level and crawl frequency."""
    statutory = default_source_registry.get_sources_by_authority(AuthorityLevel.STATUTORY_NATIONAL)
    assert len(statutory) >= 2
    for s in statutory:
        assert s.authority_level == AuthorityLevel.STATUTORY_NATIONAL

    ministry = default_source_registry.get_sources_by_authority(AuthorityLevel.CENTRAL_MINISTRY)
    assert len(ministry) >= 3

    daily = default_source_registry.get_sources_by_frequency(CrawlFrequency.DAILY)
    assert len(daily) >= 2


def test_source_registry_custom_registration(tmp_path):
    """Test registering a custom state regulator or statutory body."""
    db_file = tmp_path / "test_reg.db"
    reg = SourceRegistry(db_path=db_file)
    
    custom_src = SourceRegistryRecord(
        source_id="test_ap_pollution",
        source_organization="Andhra Pradesh Pollution Control Board",
        source_domain="pcb.ap.gov.in",
        source_type=SourceType.REGULATOR_PORTAL,
        authority_level=AuthorityLevel.STATE_GOVERNMENT,
        jurisdiction=JurisdictionCode.INDIA_ANDHRA_PRADESH,
        allowed_document_classes=[DocumentClass.GAZETTE_NOTIFICATION, DocumentClass.CERTIFICATION_GUIDANCE],
        crawl_frequency=CrawlFrequency.MONTHLY,
        parser="generic_html_parser",
        license_access_policy=LicenseStatus.PUBLIC,
        source_url="https://pcb.ap.gov.in/orders",
    )
    reg.register_source(custom_src)
    
    fetched = reg.get_source("test_ap_pollution")
    assert fetched is not None
    assert fetched.source_organization == "Andhra Pradesh Pollution Control Board"
    assert fetched.jurisdiction == JurisdictionCode.INDIA_ANDHRA_PRADESH


# ============================================================
# 2. RIGHTS & LICENSING POLICY TESTS
# ============================================================

def test_document_policy_public_domain():
    """Gazette notifications and QCOs from official domains must allow full-text indexing."""
    enforcer = default_policy_enforcer
    
    status, can_full, can_meta = enforcer.evaluate_rights(
        source_domain="egazette.gov.in",
        document_class="QCO",
    )
    assert status == LicenseStatus.PUBLIC
    assert can_full is True
    assert can_meta is True


def test_document_policy_copyrighted_standards():
    """Proprietary BIS standard specifications must allow metadata only and block full-text."""
    enforcer = default_policy_enforcer
    
    status, can_full, can_meta = enforcer.evaluate_rights(
        source_domain="standardsbis.bsbedge.com",
        document_class="STANDARD",
    )
    assert status == LicenseStatus.LICENSED
    assert can_full is False
    assert can_meta is True


def test_document_policy_unknown_source_quarantine():
    """Unknown / unverified third-party sources must be quarantined with zero indexing."""
    enforcer = default_policy_enforcer
    
    status, can_full, can_meta = enforcer.evaluate_rights(
        source_domain="random-file-share.com",
        document_class="UNKNOWN_DOC",
    )
    assert status == LicenseStatus.UNKNOWN_RIGHTS
    assert can_full is False
    assert can_meta is False


def test_document_policy_ledger_persistence(tmp_path):
    """Test policy decision recording and querying in the audit ledger."""
    db_file = tmp_path / "test_rights.db"
    enforcer = DocumentPolicyEnforcer(db_path=db_file)
    
    sample_bytes = b"Sample Gazette Notification Text 2026"
    status, can_full, can_meta = enforcer.evaluate_rights(
        source_domain="egazette.gov.in",
        document_class="GAZETTE_NOTIFICATION",
        raw_bytes=sample_bytes,
        source_url="https://egazette.gov.in/sample.pdf",
        document_id="doc_sample_123",
    )
    assert status == LicenseStatus.PUBLIC
    assert can_full is True
    
    record = enforcer.get_policy_record("doc_sample_123")
    assert record is not None
    assert record.document_id == "doc_sample_123"
    assert record.license_status == LicenseStatus.PUBLIC
    assert record.can_reproduce_full_text is True
    assert record.can_index_metadata is True
    assert record.content_hash == hashlib.sha256(sample_bytes).hexdigest()


# ============================================================
# 3. PRIORITY QUEUE TESTS
# ============================================================

def test_priority_queue_ordering():
    """Verify priority queue pops Priority 1 ahead of Priority 2, 3, 4."""
    pq = AcquisitionPriorityQueue()
    assert pq.is_empty()
    
    item_p4 = DiscoveredItem(
        item_id="item_p4",
        source_id="bis_standards_portal",
        document_class=DocumentClass.STANDARD,
        title="Voluntary Gardening Equipment Standard",
        identifier="IS 99999:2020",
        source_url="https://standardsbis.bsbedge.com/99999",
        license_status=LicenseStatus.LICENSED,
        priority=AcquisitionPriority.PRIORITY_4_GENERAL_STANDARDS,
    )
    item_p1 = DiscoveredItem(
        item_id="item_p1",
        source_id="dpiit_qco_portal",
        document_class=DocumentClass.QCO,
        title="Mandatory Steel QCO 2026",
        identifier="S.O. 9999(E)",
        source_url="https://dpiit.gov.in/qco_steel.pdf",
        license_status=LicenseStatus.PUBLIC,
        priority=AcquisitionPriority.PRIORITY_1_MANDATORY_QCO,
    )
    item_p2 = DiscoveredItem(
        item_id="item_p2",
        source_id="bis_manakonline",
        document_class=DocumentClass.PRODUCT_MANUAL,
        title="Product Manual for IS 1293",
        identifier="PM-1293",
        source_url="https://manakonline.in/pm1293",
        license_status=LicenseStatus.PUBLIC,
        priority=AcquisitionPriority.PRIORITY_2_MANUALS_AND_SCHEMES,
    )
    
    # Enqueue out of order
    pq.push(item_p4)
    pq.push(item_p1)
    pq.push(item_p2)
    
    assert len(pq) == 3
    
    # Must pop Priority 1 first
    first = pq.pop()
    assert first.item_id == "item_p1"
    assert first.priority == AcquisitionPriority.PRIORITY_1_MANDATORY_QCO
    
    # Must pop Priority 2 next
    second = pq.pop()
    assert second.item_id == "item_p2"
    assert second.priority == AcquisitionPriority.PRIORITY_2_MANUALS_AND_SCHEMES
    
    # Must pop Priority 4 last
    third = pq.pop()
    assert third.item_id == "item_p4"
    assert third.priority == AcquisitionPriority.PRIORITY_4_GENERAL_STANDARDS
    
    assert pq.is_empty()


# ============================================================
# 4. DISCOVERY ENGINE & CATALOG TESTS
# ============================================================

def test_discovery_catalog_seeded_standards():
    """Verify baseline standards are present with accurate mandatory and sector flags."""
    engine = default_discovery_engine
    
    # IS 16444 Smart Meters
    smart_meter = engine.get_standard_metadata("IS 16444:2015")
    assert smart_meter is not None
    assert smart_meter.is_mandatory is True
    assert "Smart Meter" in smart_meter.title
    assert smart_meter.category == "Electrical & Electronics"
    
    # IS 1293 Plugs & Sockets
    plugs = engine.get_standard_metadata("IS 1293:2019")
    assert plugs is not None
    assert plugs.is_mandatory is True
    assert "Plugs and Socket-Outlets" in plugs.title
    assert plugs.category == "Electrical Accessories"
    
    # IS 1417 Gold Hallmarking
    gold = engine.get_standard_metadata("IS 1417:2016")
    assert gold is not None
    assert gold.is_mandatory is True
    assert "Gold" in gold.title
    assert gold.category == "Precious Metals & Hallmarking"


def test_discovery_filtering():
    """Test filtering standards catalog by sector and mandatory status."""
    engine = default_discovery_engine
    
    mandatory_standards = engine.list_mandatory_standards()
    assert len(mandatory_standards) >= 6
    for s in mandatory_standards:
        assert s.is_mandatory is True
        assert s.mandatory_qco_number is not None

    elec_standards = engine.get_standards_by_category("Electrical Accessories")
    assert len(elec_standards) >= 1
    assert elec_standards[0].standard_number == "IS 1293:2019"


# ============================================================
# 5. KNOWLEDGE GRAPH RELATIONSHIPS TESTS
# ============================================================

def test_knowledge_graph_entities_and_relationships():
    """Verify SQLite graph entities (QCOs, Schemes, Products) and relationships."""
    repo = default_repository
    
    # Check Authorities
    authorities = repo._conn.execute("SELECT * FROM authorities").fetchall()
    auth_ids = {a["authority_id"] for a in authorities}
    assert "BIS" in auth_ids
    assert "DPIIT" in auth_ids
    assert "MeitY" in auth_ids
    assert "MoCA" in auth_ids
    
    # Check Jurisdictions
    jurisdictions = repo._conn.execute("SELECT * FROM jurisdictions").fetchall()
    jur_codes = {j["jurisdiction_code"] for j in jurisdictions}
    assert "INDIA:NATIONAL" in jur_codes
    assert "INDIA:AP" in jur_codes
    
    # Check Certification Schemes
    schemes = repo._conn.execute("SELECT * FROM certification_schemes").fetchall()
    scheme_ids = {s["scheme_id"] for s in schemes}
    assert "SCHEME_1" in scheme_ids  # ISI Mark
    assert "SCHEME_2" in scheme_ids  # CRS
    assert "SCHEME_4" in scheme_ids  # Hallmarking
    
    # Check QCOs
    qcos = repo._conn.execute("SELECT * FROM qcos").fetchall()
    qco_ids = {q["qco_id"] for q in qcos}
    assert "qco_gold_hallmarking" in qco_ids
    assert "qco_smart_meter" in qco_ids
    assert "qco_plugs_sockets" in qco_ids
    assert "qco_led_drivers" in qco_ids
    
    # Check Graph Relationships
    rels = repo._conn.execute("SELECT * FROM knowledge_relationships").fetchall()
    assert len(rels) >= 15
    
    # Verify specific relationship links
    qco_to_std = [r for r in rels if r["source_entity_id"] == "qco_gold_hallmarking" and r["relationship_type"] == "APPLIES_TO"]
    assert len(qco_to_std) == 1
    assert qco_to_std[0]["target_entity_id"] == "IS 1417:2016"
    
    std_to_scheme = [r for r in rels if r["source_entity_id"] == "IS 1417:2016" and r["relationship_type"] == "CERTIFIED_UNDER"]
    assert len(std_to_scheme) == 1
    assert std_to_scheme[0]["target_entity_id"] == "SCHEME_4"


# ============================================================
# 6. UPDATE ENGINE & DEDUPLICATION TESTS
# ============================================================

def test_update_engine_hash_deduplication():
    """Verify identical document content is identified by hash and not reprocessed."""
    raw_pdf_bytes = b"%PDF-1.4 Mock Gazette Notification Data For Hash Test"
    h1 = UpdateEngine.compute_content_hash(raw_pdf_bytes)
    h2 = UpdateEngine.compute_content_hash(raw_pdf_bytes)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex string


def test_sync_evaluation_result():
    """Test quality gate evaluation data structure."""
    res = SyncEvaluationResult(
        passed=True,
        checks={"schema_6_compliant": True, "rights_authorized": True, "non_empty_chunks": True},
        details={"chunk_count": 95, "license": "PUBLIC"}
    )
    d = res.to_dict()
    assert d["passed"] is True
    assert d["checks"]["schema_6_compliant"] is True
    assert d["details"]["chunk_count"] == 95


# ============================================================
# 7. COVERAGE REPORTER METRICS TESTS
# ============================================================

def test_coverage_reporter_live_metrics():
    """Verify CoverageReporter aggregates live data from SQLite and ChromaDB without error."""
    reporter = default_coverage_reporter
    metrics = reporter.get_metrics()
    
    assert metrics["sources"]["total"] >= 7
    assert metrics["rights"]["total"] >= 8
    assert metrics["catalog"]["total_standards"] >= 7
    assert metrics["graph"]["relationships_total"] >= 15
    assert metrics["vector_store"]["total_chunks"] >= 95
    assert metrics["vector_store"]["schema_compliant"] is True
    
    markdown = reporter.generate_markdown_report()
    assert "# BIS KNOWLEDGE COVERAGE & CORPUS INVENTORY AUDIT" in markdown
    assert "Chunks" in markdown
    assert "IS 1417:2016" in markdown
    assert "IS 16444:2015" in markdown
    assert "IS 1293:2019" in markdown
    assert "APPLIES_TO" in markdown

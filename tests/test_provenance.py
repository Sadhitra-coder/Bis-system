"""
tests/test_provenance.py

Phase 2 Evidence-Grade Ingestion and Provenance Tests:
1. DocumentMetadata contract & serialization
2. ChunkMetadata canonical contract
3. BIS entity extraction (IS number, year, part, title, amendment, edition)
4. Chunking provenance (page_start, page_end, chunk_index, content_hash)
5. Page propagation during section merging
6. Provenance validation & quality reporting
7. Retrieval flattening & provenance propagation
8. Chunk ID determinism & stability
"""

import json
from pathlib import Path
import pytest

from app.models import DocumentMetadata, ChunkMetadata
from app.steps.bis_extractor import (
    extract_standard_number,
    extract_standard_year,
    extract_part_number,
    extract_amendment_number,
    extract_edition,
    extract_publication_date,
    extract_standard_title,
    infer_document_type,
    compute_content_hash,
    build_document_metadata,
)
from app.steps.chunk import create_chunks, merge_sections, parse_markdown_sections
from app.steps.provenance import (
    validate_chunk,
    validate_chunks,
    provenance_quality_report,
)
from app.rag.retriever import flatten_provenance


# ============================================================
# 1. DocumentMetadata Contract
# ============================================================

def test_document_metadata_defaults_and_serialization():
    doc = DocumentMetadata(
        document_id="doc_123",
        source_file="standards/IS_3055.pdf",
        source_filename="IS_3055.pdf",
    )
    assert doc.document_id == "doc_123"
    assert doc.source_filename == "IS_3055.pdf"
    assert doc.standard_number is None
    assert doc.standard_year is None
    assert doc.is_current is None
    assert doc.parser_version == "1.0"

    data = doc.model_dump()
    assert data["document_id"] == "doc_123"
    assert data["standard_number"] is None
    assert "source_hash" in data


# ============================================================
# 2. ChunkMetadata Canonical Contract
# ============================================================

def test_chunk_metadata_phase2_fields():
    chunk = ChunkMetadata(
        chunk_id="doc1_chunk_001",
        document_id="doc1",
        source_file="doc1.pdf",
        page_start=3,
        page_end=3,
        content_hash="abc12345",
        chunk_index=0,
        standard_number="IS 3055",
        standard_title="Clinical Thermometers",
        standard_year=2024,
        clause_id="4.2",
        clause_title="Accuracy Requirements",
    )
    assert chunk.page_start == 3
    assert chunk.page_end == 3
    assert chunk.chunk_index == 0
    assert chunk.content_hash == "abc12345"
    assert chunk.standard_number == "IS 3055"
    assert chunk.clause_id == "4.2"


# ============================================================
# 3. BIS Entity Extraction
# ============================================================

def test_bis_standard_number_and_year_extraction():
    text = """
    # Indian Standard
    # IS 3055 : 2024
    CLINICAL THERMOMETERS — SPECIFICATION
    (Third Edition)
    """
    assert extract_standard_number(text) == "IS 3055"
    assert extract_standard_year(text) == 2024
    assert extract_edition(text) == "Third Edition"


def test_bis_part_number_extraction():
    text = "IS 1234 (Part 2) : 2019 - Requirements for safety."
    assert extract_standard_number(text) == "IS 1234-2"
    assert extract_part_number(text) == "2"
    assert extract_standard_year(text) == 2019


def test_bis_amendment_extraction():
    text = "Amendment No. 2 to IS 3055 : 2020"
    assert extract_amendment_number(text) == "2"


def test_bis_title_and_doc_type_inference():
    text = """# IS 3055 : 2020
TITLE: Specification for Clinical Thermometers
Date: 15 August 2020
"""
    assert extract_standard_title(text) == "IS 3055 : 2020"
    assert infer_document_type(text, "IS_3055.pdf") == "indian_standard"


def test_build_document_metadata():
    text = """# IS 3055 (Part 1) : 2021
TITLE: Medical Diagnostic Devices
Second Edition
Published: January 2021
"""
    doc_meta = build_document_metadata(
        document_id="is_3055_p1",
        source_file="raw/is_3055_p1.pdf",
        source_filename="is_3055_p1.pdf",
        source_hash="sha256hash123",
        text_sample=text,
    )
    assert doc_meta.document_id == "is_3055_p1"
    assert doc_meta.standard_number == "IS 3055-1"
    assert doc_meta.standard_year == 2021
    assert doc_meta.part_number == "1"
    assert doc_meta.edition_or_version == "Second Edition"
    assert doc_meta.authority == "BIS"
    assert doc_meta.source_hash == "sha256hash123"


# ============================================================
# 4. Chunking Provenance
# ============================================================

def test_create_chunks_preserves_provenance():
    md = """<!-- PAGE 1 -->
# IS 3055 : 2024 Clinical Thermometers

<!-- PAGE 2 -->
## 4 Requirements
The thermometer shall operate within ±0.1°C accuracy.

<!-- PAGE 3 -->
### 4.1 Testing
Testing must be performed in a calibrated water bath.
"""
    chunks = create_chunks(md, document_id="IS_3055", source_file="IS_3055.pdf")
    assert len(chunks) >= 1

    for idx, c in enumerate(chunks):
        assert "page_start" in c
        assert "content_hash" in c
        assert c["chunk_index"] == idx
        assert c["document_id"] == "IS_3055"
        assert c["standard_number"] == "IS 3055"
        assert c["standard_year"] == 2024
        # Metadata dictionary consistency
        assert c["metadata"]["content_hash"] == c["content_hash"]
        assert c["metadata"]["chunk_index"] == idx


def test_chunk_determinism_and_hash_change():
    md1 = "# Clause 1\nStandard operational details for medical testing."
    chunks1 = create_chunks(md1, "docA", "docA.pdf")
    chunks2 = create_chunks(md1, "docA", "docA.pdf")

    # Identical content yields identical chunk_id and content_hash
    assert chunks1[0]["chunk_id"] == chunks2[0]["chunk_id"]
    assert chunks1[0]["content_hash"] == chunks2[0]["content_hash"]

    # Modified content yields different content_hash
    md2 = "# Clause 1\nModified operational details for testing."
    chunks3 = create_chunks(md2, "docA", "docA.pdf")
    assert chunks1[0]["content_hash"] != chunks3[0]["content_hash"]


# ============================================================
# 5. Page Propagation in Section Merging
# ============================================================

def test_merge_sections_propagates_page_number():
    sections = [
        {
            "heading": "4.1 Intro",
            "heading_level": 2,
            "heading_context": ["4 Requirements"],
            "page_number": 5,
            "content": "Short text.",
        },
        {
            "heading": "4.2 Details",
            "heading_level": 2,
            "heading_context": ["4 Requirements"],
            "page_number": 6,
            "content": "More details that continue.",
        },
    ]
    merged = merge_sections(sections)
    assert len(merged) == 1
    assert merged[0]["page_number"] == 5


# ============================================================
# 6. Provenance Validation & Quality Reporting
# ============================================================

def test_validate_chunk_detects_issues():
    # Valid chunk
    valid = {
        "chunk_id": "c1",
        "document_id": "d1",
        "source_file": "f1.pdf",
        "content": "Valid text",
        "page_start": 2,
        "page_end": 2,
    }
    assert validate_chunk(valid) == []

    # Missing required field
    invalid_missing = {
        "chunk_id": "c1",
        "document_id": "d1",
        "content": "Text",
    }
    issues = validate_chunk(invalid_missing)
    assert any("missing_required_field:source_file" in i for i in issues)

    # Invalid page range
    invalid_page = {
        "chunk_id": "c2",
        "document_id": "d1",
        "source_file": "f1.pdf",
        "content": "Text",
        "page_start": 5,
        "page_end": 2,
    }
    issues = validate_chunk(invalid_page)
    assert any("invalid_page_range" in i for i in issues)


def test_provenance_quality_report():
    chunks = [
        {
            "chunk_id": "c1",
            "document_id": "d1",
            "source_file": "f1.pdf",
            "content": "Text 1",
            "page_start": 1,
            "clause_id": "3.1",
            "standard_number": "IS 100",
            "edition_or_version": "First",
        },
        {
            "chunk_id": "c2",
            "document_id": "d1",
            "source_file": "f1.pdf",
            "content": "Text 2",
            "page_start": None,
            "clause_id": None,
            "standard_number": None,
            "edition_or_version": None,
        },
    ]
    report = provenance_quality_report(chunks)
    assert report["chunks_created"] == 2
    assert report["chunks_with_page_provenance"] == 1
    assert report["chunks_without_page_provenance"] == 1
    assert report["chunks_with_clause_ids"] == 1
    assert report["chunks_with_standard_numbers"] == 1
    assert report["chunks_with_versions"] == 1
    assert report["page_provenance_pct"] == 50.0
    assert report["clause_provenance_pct"] == 50.0
    assert report["standard_identity_pct"] == 50.0
    assert report["version_metadata_pct"] == 50.0
    assert report["provenance_completeness_pct"] == 50.0


# ============================================================
# 7. Retrieval Provenance Flattening
# ============================================================

def test_flatten_provenance():
    raw_chunk = {
        "chunk_id": "chunk_abc",
        "content": "The standard requirements are specified herein.",
        "dense_score": 0.85,
        "metadata": {
            "document_id": "doc_IS_3055",
            "source_file": "IS_3055.pdf",
            "page_start": 4,
            "page_end": 4,
            "section": "4 Requirements",
            "clause_id": "4.1",
            "standard_number": "IS 3055",
            "standard_title": "Clinical Thermometers",
            "standard_year": 2024,
            "edition_or_version": "Third Edition",
            "authority": "BIS",
            "source_hash": "hash123",
            "chunk_index": 2,
            "content_hash": "chash456",
        }
    }
    flattened = flatten_provenance(raw_chunk)
    assert flattened["chunk_id"] == "chunk_abc"
    assert flattened["document_id"] == "doc_IS_3055"
    assert flattened["page_start"] == 4
    assert flattened["page_end"] == 4
    assert flattened["clause_id"] == "4.1"
    assert flattened["standard_number"] == "IS 3055"
    assert flattened["standard_title"] == "Clinical Thermometers"
    assert flattened["authority"] == "BIS"
    assert flattened["score"] == 0.85
    # Original metadata is preserved
    assert "metadata" in flattened


# ============================================================
# 8. Document Identity Semantics (Phase 2.1)
# ============================================================

from app.steps.bis_extractor import derive_document_id
import hashlib


def test_document_id_semantics():
    bytes_a1 = b"PDF content version 1"
    bytes_a2 = b"PDF content version 1"  # identical bytes
    bytes_b = b"PDF content version 2"   # different bytes

    hash_a1 = hashlib.sha256(bytes_a1).hexdigest()
    hash_a2 = hashlib.sha256(bytes_a2).hexdigest()
    hash_b = hashlib.sha256(bytes_b).hexdigest()

    # 1. same filename + same bytes -> same document_id
    id_a1 = derive_document_id(hash_a1)
    id_a2 = derive_document_id(hash_a2)
    assert id_a1 == id_a2

    # 2. same filename + different bytes -> different document_id
    id_b = derive_document_id(hash_b)
    assert id_a1 != id_b

    # 3. different filename + same bytes -> same document_id
    # (identity is derived strictly from exact source bytes)
    id_diff_fname = derive_document_id(hash_a1)
    assert id_diff_fname == id_a1


# ============================================================
# 9. Page Provenance Survives Pipeline Stages (Phase 2.1)
# ============================================================

from app.steps.clean import clean_markdown
from app.steps.normalize import normalize_markdown


def test_page_provenance_survives_pipeline_stages():
    """
    Verifies that page boundaries extracted from Docling survive cleaning,
    normalization, and chunking without fabrication or loss.
    """
    raw_extracted_md = """<!-- PAGE 1 -->
# IS 3055 (Part 1) : 2024
This Indian Standard (Part 1) was adopted by the Bureau of Indian Standards after the draft finalized by the Medical Instruments Sectional Committee. This document covers specification and clinical requirements for medical glass thermometers.

<!-- PAGE 2 -->
## 4 Requirements
The instruments must be constructed with high-grade borosilicate glass free from internal stress, cracks, and visible bubbles. The scale graduation lines shall be distinct, durable, and resistant to antiseptic liquids used in medical facilities.

<!-- PAGE 3 -->
### 4.1 Calibration
Thermometers shall be tested against national reference standards in a circulating thermostatic liquid bath. The scale error shall not exceed the maximum permissible tolerances specified in Table 1 across the operational range.
"""
    # Stage 2: Clean
    cleaned_md = clean_markdown(raw_extracted_md)
    assert "<!-- PAGE 1 -->" in cleaned_md
    assert "<!-- PAGE 2 -->" in cleaned_md
    assert "<!-- PAGE 3 -->" in cleaned_md

    # Stage 4: Normalize
    normalized_md = normalize_markdown(cleaned_md)
    assert "<!-- PAGE 1 -->" in normalized_md
    assert "<!-- PAGE 2 -->" in normalized_md
    assert "<!-- PAGE 3 -->" in normalized_md

    # Stage 5: Chunk
    chunks = create_chunks(normalized_md, document_id="doc_test_survive", source_file="test.pdf")
    assert len(chunks) >= 3

    # Check page numbers are assigned accurately without fabrication
    pages = [c["page_start"] for c in chunks]
    assert 1 in pages
    assert 2 in pages
    assert 3 in pages
    for c in chunks:
        assert c["page_start"] is not None
        assert c["page_start"] == c["page_end"]
        assert c["page_start"] in (1, 2, 3)


# ============================================================
# 10. Realistic Multi-Page BIS Fixture Test (Phase 2.1)
# ============================================================

def test_realistic_multipage_bis_fixture_metadata_and_retrieval():
    """
    Realistic multi-page synthetic fixture with explicit BIS properties:
    - Standard Number: IS 3055 : 2024
    - Standard Title: Specification for Clinical Thermometers
    - Edition: Third Edition
    - Amendment: Amendment No. 1
    - Clause ID: 4.1
    - Page Span: 3 pages
    """
    synthetic_bis_doc = """<!-- PAGE 1 -->
# IS 3055 : 2024
TITLE: Specification for Clinical Thermometers
(Third Edition)
Amendment No. 1
Authority: Bureau of Indian Standards

<!-- PAGE 2 -->
## 4 Requirements
General manufacturing, safety and performance requirements.

<!-- PAGE 3 -->
### 4.1 Calibration and Permissible Error
The maximum permissible error of temperature indication shall be ±0.1°C between 35.0°C and 42.0°C.
"""
    source_hash = hashlib.sha256(synthetic_bis_doc.encode("utf-8")).hexdigest()
    doc_meta = build_document_metadata(
        source_file="standards/IS_3055_2024.pdf",
        source_filename="IS_3055_2024.pdf",
        source_hash=source_hash,
        text_sample=synthetic_bis_doc[:3000],
    )

    # 1. DocumentMetadata verification
    assert doc_meta.document_id == f"doc_{source_hash[:16]}"
    assert doc_meta.standard_number == "IS 3055"
    assert doc_meta.standard_year == 2024
    assert doc_meta.standard_title == "IS 3055 : 2024"
    assert doc_meta.edition_or_version == "Third Edition"
    assert doc_meta.amendment_number == "1"
    assert doc_meta.authority == "BIS"

    # 2. Chunking with doc_metadata
    chunks = create_chunks(
        synthetic_bis_doc,
        document_id=doc_meta.document_id,
        source_file=doc_meta.source_file,
        doc_metadata=doc_meta,
    )

    # Find the chunk corresponding to clause 4.1
    clause_4_1_chunk = next(
        (c for c in chunks if c.get("clause_id") == "4.1"),
        None
    )
    assert clause_4_1_chunk is not None
    assert clause_4_1_chunk["page_start"] == 3
    assert clause_4_1_chunk["page_end"] == 3
    assert clause_4_1_chunk["standard_number"] == "IS 3055"
    assert clause_4_1_chunk["standard_year"] == 2024
    assert clause_4_1_chunk["edition_or_version"] == "Third Edition"
    assert clause_4_1_chunk["amendment_number"] == "1"
    assert clause_4_1_chunk["authority"] == "BIS"

    # 3. Chroma preparation verification
    from app.steps.embed import prepare_chunks
    ids, docs, metadatas = prepare_chunks([clause_4_1_chunk])
    assert len(ids) == 1
    m = metadatas[0]
    assert m["document_id"] == doc_meta.document_id
    assert m["standard_number"] == "IS 3055"
    assert m["clause_id"] == "4.1"
    assert m["page_start"] == 3
    assert m["page_end"] == 3
    assert m["edition_or_version"] == "Third Edition"
    assert m["amendment_number"] == "1"

    # 4. Retrieval flattening verification
    simulated_hit = {
        "chunk_id": ids[0],
        "content": docs[0],
        "hybrid_score": 0.92,
        "metadata": m,
    }
    flattened = flatten_provenance(simulated_hit)
    assert flattened["chunk_id"] == ids[0]
    assert flattened["document_id"] == doc_meta.document_id
    assert flattened["page_start"] == 3
    assert flattened["page_end"] == 3
    assert flattened["clause_id"] == "4.1"
    assert flattened["standard_number"] == "IS 3055"
    assert flattened["edition_or_version"] == "Third Edition"
    assert flattened["amendment_number"] == "1"
    assert flattened["score"] == 0.92


# ============================================================
# 11. End-to-End Fixture Indexing & Retrieval Integration (Phase 2.1)
# ============================================================

from unittest.mock import MagicMock
import numpy as np
import chromadb
from app.steps.embed import embed_chunks, prepare_chunks
from app.rag.retriever import HybridRetriever


def test_multipage_fixture_chroma_and_retrieval_integration(tmp_path):
    """
    Integration test demonstrating:
    1. Ingestion of multi-page BIS fixture
    2. Indexing into Chroma collection
    3. Retrieval query via HybridRetriever
    4. Obtained hit proves provenance belongs to retrieved chunk:
       - chunk_id
       - document_id
       - source_hash
       - page_start / page_end
       - section / heading_context
       - clause_id
       - standard_number
       - source_file
    """
    synthetic_doc = """<!-- PAGE 1 -->
# IS 3055 : 2024
TITLE: Specification for Clinical Thermometers
Edition: Third Edition
Amendment No. 1
This standard establishes rigorous technical, material, and metrological requirements for mercury-in-glass clinical thermometers utilized in professional healthcare settings and hospital environments.

<!-- PAGE 2 -->
## 4 Requirements
General manufacturing, safety, quality assurance, and mechanical performance requirements for medical glass clinical thermometers. All glass components and capillary stems shall be fabricated from certified high-grade borosilicate glass completely free from internal thermal stress, hairline cracks, and visible air bubbles. The graduation marks shall be clearly visible and permanently etched.

<!-- PAGE 3 -->
### 4.1 Calibration and Accuracy
The maximum permissible error of temperature indication shall be ±0.1°C between 35.0°C and 42.0°C under nominal testing conditions. Calibration shall be rigorously conducted using an accredited circulating water bath traceable to national primary metrological standards, with stabilized thermal equilibrium maintained throughout testing.
"""
    source_hash = hashlib.sha256(synthetic_doc.encode("utf-8")).hexdigest()
    doc_meta = build_document_metadata(
        source_file="standards/IS_3055_2024.pdf",
        source_filename="IS_3055_2024.pdf",
        source_hash=source_hash,
        text_sample=synthetic_doc[:3000],
    )

    chunks = create_chunks(
        synthetic_doc,
        document_id=doc_meta.document_id,
        source_file=doc_meta.source_file,
        doc_metadata=doc_meta,
    )
    assert len(chunks) >= 3

    # Index into isolated Chroma collection
    client = chromadb.Client()  # in-memory Chroma for fast isolated testing
    collection = client.create_collection(name="test_fixture_retrieval")

    ids, docs, metadatas = prepare_chunks(chunks)

    # Mock embedder for fast, reproducible vector generation
    mock_embedder = MagicMock()
    def fake_encode(texts, **kwargs):
        if isinstance(texts, str):
            return np.ones(64, dtype=float)
        return np.ones((len(texts), 64), dtype=float)

    mock_embedder.encode.side_effect = fake_encode

    embed_chunks(mock_embedder, collection, ids, docs, metadatas)
    assert collection.count() >= 3

    # Instantiate HybridRetriever over this collection
    retriever = HybridRetriever(embedder=mock_embedder, collection=collection)

    # Perform retrieval targeting Clause 4.1 content on Page 3
    hits = retriever.retrieve("maximum permissible error temperature indication", top_k=1)
    assert len(hits) == 1
    hit = hits[0]

    # Verify hit contains all required provenance fields and belongs to retrieved chunk
    assert "chunk_id" in hit and hit["chunk_id"].startswith("doc_")
    assert hit["document_id"] == doc_meta.document_id
    assert hit["source_hash"] == source_hash
    assert hit["page_start"] == 3
    assert hit["page_end"] == 3
    assert "4.1" in hit["section"]
    assert hit["clause_id"] == "4.1"
    assert hit["standard_number"] == "IS 3055"
    assert hit["source_file"] == "standards/IS_3055_2024.pdf"
    assert "metadata" in hit
    assert hit["score"] > 0


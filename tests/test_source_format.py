"""
tests/test_source_format.py

Phase 6 section 12: standard identity in LLM context and API sources.

The bug: both formatters read metadata.get("standard") and
metadata.get("title") — keys no writer in the repository has ever produced.
The canonical fields are standard_number and standard_title. Every one of
these tests would have passed vacuously before, because the old code
produced a header and a source object that were merely EMPTY rather than
wrong, which is why the defect survived a full test suite.
"""

import pytest

from app.index_schema import UNKNOWN_INT
from app.rag.generator import AnswerGenerator
from app.rag.pipeline import RAGPipeline
from app.rag.source_format import (
    SourceIdentity,
    build_sources,
    extract_source_identity,
    format_source_header,
    source_to_dict,
)


def standard_result(**overrides):
    """A retrieval result in RetrievalResult.to_dict() shape."""
    result = {
        "chunk_id": "chunk_a",
        "document_id": "doc_is3055",
        "source_file": "standards/IS_3055_2024.pdf",
        "content": "The pipe shall withstand 10 bar.",
        "section": "4.1 Requirements",
        "clause_id": "4.1",
        "clause_title": "Pressure requirements",
        "standard_number": "IS 3055",
        "standard_title": "Code of practice for water supply",
        "standard_year": 2024,
        "edition_or_version": "Third Edition",
        "part_number": None,
        "amendment_number": None,
        "authority": "BIS",
        "document_type": "indian_standard",
        "page_start": 7,
        "page_end": 7,
        "standard_id": "std_IS_3055_abc123",
        "version_id": "ver_std_IS_3055_abc123_def456",
        "standard_relation": "identity",
        "metadata": {},
    }
    result.update(overrides)
    return result


# ------------------------------------------------------------------
# Extraction
# ------------------------------------------------------------------

def test_identity_is_read_from_canonical_field_names():
    identity = extract_source_identity(standard_result())
    assert identity.standard_number == "IS 3055"
    assert identity.standard_title == "Code of practice for water supply"
    assert identity.standard_year == 2024
    assert identity.edition_or_version == "Third Edition"
    assert identity.clause_id == "4.1"
    assert identity.page_start == 7
    assert identity.is_standard is True


def test_the_old_keys_are_not_consulted():
    """
    A result carrying only the never-written keys must produce no standard
    identity. Reading them again would resurrect the bug in a new place.
    """
    identity = extract_source_identity({
        "chunk_id": "chunk_x",
        "content": "text",
        "standard": "IS 9999",
        "title": "Something",
        "metadata": {"standard": "IS 9999", "title": "Something"},
    })
    assert identity.standard_number is None
    assert identity.standard_title is None


def test_metadata_is_the_fallback_when_top_level_is_absent():
    identity = extract_source_identity({
        "chunk_id": "chunk_m",
        "content": "text",
        "metadata": {
            "standard_number": "IS 456",
            "standard_title": "Plain and reinforced concrete",
            "page_start": 3,
            "standard_relation": "identity",
        },
    })
    assert identity.standard_number == "IS 456"
    assert identity.standard_title == "Plain and reinforced concrete"
    assert identity.page_start == 3


def test_empty_strings_are_absence_not_content():
    """Chroma cannot store null, so the index writes ''. That is unknown."""
    identity = extract_source_identity(standard_result(
        standard_number="", clause_id="", edition_or_version="   ",
    ))
    assert identity.standard_number is None
    assert identity.clause_id is None
    assert identity.edition_or_version is None


def test_unknown_int_sentinel_does_not_become_a_page_number():
    identity = extract_source_identity(standard_result(
        page_start=UNKNOWN_INT, page_end=UNKNOWN_INT, standard_year=UNKNOWN_INT,
    ))
    assert identity.page_start is None
    assert identity.page_end is None
    assert identity.standard_year is None
    assert identity.page_label() is None


def test_page_zero_is_a_real_page_not_unknown():
    identity = extract_source_identity(standard_result(page_start=0, page_end=0))
    assert identity.page_start == 0
    assert identity.page_label() == "p. 0"


def test_extraction_tolerates_garbage_input():
    assert extract_source_identity(None).standard_number is None
    assert extract_source_identity("not a dict").standard_number is None
    assert extract_source_identity({"metadata": "not a dict"}).standard_number is None


# ------------------------------------------------------------------
# Labels and citations
# ------------------------------------------------------------------

def test_standard_label_includes_part_and_year():
    identity = extract_source_identity(standard_result(part_number="2"))
    assert identity.standard_label() == "IS 3055 (Part 2) : 2024"


def test_standard_label_is_none_without_a_number():
    """An unnumbered document must not be given a fabricated designation."""
    assert extract_source_identity(standard_result(standard_number=None)).standard_label() is None


def test_citation_reads_like_a_citation():
    identity = extract_source_identity(standard_result())
    assert identity.citation() == (
        "IS 3055 : 2024, Third Edition, Clause 4.1, p. 7"
    )


def test_citation_page_range():
    identity = extract_source_identity(standard_result(page_start=7, page_end=9))
    assert "p. 7-9" in identity.citation()


def test_citation_falls_back_when_no_standard_identity():
    identity = extract_source_identity({
        "chunk_id": "chunk_z", "source_file": "manuals/pump.pdf", "content": "x",
    })
    assert identity.citation() == "manuals/pump.pdf"


def test_citation_of_a_completely_anonymous_chunk():
    assert extract_source_identity({"content": "x"}).citation() == "unidentified source"


def test_dedup_key_separates_pages_within_one_section():
    """
    Two passages from the same section on different pages are different
    citations. Collapsing them would attribute a requirement to the wrong page.
    """
    a = extract_source_identity(standard_result(page_start=7, page_end=7))
    b = extract_source_identity(standard_result(page_start=8, page_end=8))
    assert a.dedup_key() != b.dedup_key()


# ------------------------------------------------------------------
# LLM grounding header
# ------------------------------------------------------------------

def test_header_names_the_standard_for_the_model():
    header = format_source_header(extract_source_identity(standard_result()))
    assert "Standard: IS 3055 : 2024" in header
    assert "Title: Code of practice for water supply" in header
    assert "Edition: Third Edition" in header
    assert "Clause: 4.1 Pressure requirements" in header
    assert "Page: p. 7" in header


def test_header_labels_a_citation_as_a_citation():
    """
    A pump manual quoting IS 3055 is not IS 3055. 'Standard: IS 3055' on
    that passage would invite exactly the misattribution the knowledge model
    exists to prevent.
    """
    header = format_source_header(extract_source_identity(standard_result(
        document_type="product_manual",
        standard_relation="reference",
        standard_id="",
        version_id="",
        edition_or_version="Third Edition",
    )))
    assert "References standard: IS 3055 : 2024" in header
    assert "Standard: IS 3055" not in header
    # An edition belongs to the standard, not to the document citing it.
    assert "Edition:" not in header


def test_header_omits_unknown_fields_entirely():
    header = format_source_header(extract_source_identity({
        "chunk_id": "chunk_bare",
        "document_id": "doc_bare",
        "content": "x",
    }))
    assert header == "Document: doc_bare"
    assert "Standard" not in header
    assert "Page" not in header


def test_header_of_an_empty_identity_says_so():
    assert format_source_header(SourceIdentity()) == "Source information not available."


# ------------------------------------------------------------------
# API source objects
# ------------------------------------------------------------------

def test_api_source_carries_standard_identity():
    source = source_to_dict(extract_source_identity(standard_result()))
    assert source["standard_number"] == "IS 3055"
    assert source["standard_title"] == "Code of practice for water supply"
    assert source["standard_year"] == 2024
    assert source["clause_id"] == "4.1"
    assert source["page_start"] == 7
    assert source["standard_id"] == "std_IS_3055_abc123"
    assert source["version_id"] == "ver_std_IS_3055_abc123_def456"
    assert source["standard_relation"] == "identity"
    assert source["citation"] == "IS 3055 : 2024, Third Edition, Clause 4.1, p. 7"


def test_api_source_omits_unknown_but_always_states_the_relation():
    source = source_to_dict(extract_source_identity({
        "chunk_id": "chunk_bare", "document_id": "doc_bare", "content": "x",
    }))
    assert "standard_number" not in source
    assert "page_start" not in source
    # A consumer must be able to tell a standard from a citation without
    # inspecting every other field.
    assert source["standard_relation"] == "none"


def test_build_sources_dedupes_and_drops_anonymous_results():
    sources = build_sources([
        standard_result(),
        standard_result(),                       # exact duplicate
        standard_result(page_start=9, page_end=9),  # different page: kept
        {"content": "no identity at all"},       # dropped
        "not a dict",                            # dropped
    ])
    assert len(sources) == 2
    assert {s["page_start"] for s in sources} == {7, 9}


def test_build_sources_handles_none():
    assert build_sources(None) == []


# ------------------------------------------------------------------
# The two real call sites
# ------------------------------------------------------------------

def test_generator_format_context_exposes_standard_identity():
    """
    The regression that matters: before the fix this context block named no
    standard, so the model could not attribute a requirement to IS 3055
    rather than to another standard in the same window.
    """
    generator = AnswerGenerator.__new__(AnswerGenerator)  # no LLM client needed
    context = AnswerGenerator.format_context(generator, [standard_result()])

    assert "Standard: IS 3055 : 2024" in context
    assert "Clause: 4.1 Pressure requirements" in context
    assert "The pipe shall withstand 10 bar." in context


def test_generator_and_pipeline_agree_on_the_standard_name():
    """
    One helper, two renderings. If these ever disagree, the citation the
    model was given and the citation returned to the caller have drifted.
    """
    generator = AnswerGenerator.__new__(AnswerGenerator)
    context = AnswerGenerator.format_context(generator, [standard_result()])

    pipeline = RAGPipeline.__new__(RAGPipeline)
    sources = RAGPipeline._build_sources(pipeline, [standard_result()])

    label = sources[0]["standard_number"]
    assert label == "IS 3055"
    assert label in context
    assert sources[0]["citation"].startswith("IS 3055 : 2024")


def test_generator_skips_results_without_content():
    generator = AnswerGenerator.__new__(AnswerGenerator)
    context = AnswerGenerator.format_context(generator, [
        standard_result(content=""),
        standard_result(chunk_id="chunk_b"),
    ])
    assert context.count("RETRIEVED SOURCE") == 1


def test_generator_format_context_on_empty_input():
    generator = AnswerGenerator.__new__(AnswerGenerator)
    assert AnswerGenerator.format_context(generator, []) == ""

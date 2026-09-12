"""
tests/test_confidence.py

Phase 7 Failure-First Unit Tests for Evidence Confidence, Abstention,
and Verification-Required decisions (Section 22).

Covers all 18 test requirements:
  1. strong exact clause evidence -> high confidence
  2. strong semantic evidence -> answerable
  3. weak semantic evidence -> lower confidence
  4. no relevant evidence -> verification_required
  5. conflicting evidence -> verification_required
  6. ambiguous query -> verification_required
  7. exact standard mismatch -> verification_required
  8. exact version mismatch -> lower confidence
  9. incomplete provenance -> reduced confidence
  10. multiple supporting chunks -> increased evidence strength
  11. duplicate chunks -> do not falsely count as independent evidence
  12. no evidence != negative evidence (epistemic distinction)
  13. standard-level query -> title/version evidence satisfies
  14. clause-specific query -> generic header evidence alone cannot produce high confidence
  15. amendment query -> amendment evidence required for high confidence
  16. Phase 4.2 ranking remains intact
  17. source_content remains authoritative
  18. contextualized_content remains retrieval-only
"""

import pytest
from app.confidence import (
    ConfidenceLevel,
    ConfidenceResult,
    Decision,
    EvidenceEvaluator,
    EvidenceItem,
    QueryState,
)
from app.evidence.models import compute_provenance_completeness
from app.rag.reranker import Reranker
from app.rag.query import RetrievalResult


def _make_evidence(
    chunk_id: str = "c1",
    document_id: str = "doc_3055",
    standard_number: str = "IS 3055",
    clause_id: str = "4.1",
    page_start: int = 3,
    reranker_score: float = 3.5,
    content: str = None,
    source_content: str = None,
    contextualized_content: str = None,
    provenance_completeness: float = 1.0,
    amendment_number: str = "",
    standard_year: int = 2024,
    retrieval_methods: list = None,
    dense_rank: int = 1,
    bm25_rank: int = 1,
) -> EvidenceItem:
    text = source_content or content or "Clinical thermometers shall not exceed error of 0.1 C."
    return EvidenceItem(
        chunk_id=chunk_id,
        document_id=document_id,
        source_hash=f"hash_{chunk_id}",
        source_file="standards/IS_3055.pdf",
        standard_number=standard_number,
        standard_title="Clinical Thermometers",
        clause_id=clause_id,
        clause_title="Calibration and Accuracy",
        page_start=page_start,
        page_end=page_start,
        content=text,
        source_content=text,
        contextualized_content=contextualized_content or f"[Standard: {standard_number}] {text}",
        reranker_score=reranker_score,
        provenance_completeness=provenance_completeness,
        amendment_number=amendment_number,
        standard_year=standard_year,
        retrieval_methods=retrieval_methods or ["dense", "bm25"],
        dense_rank=dense_rank,
        bm25_rank=bm25_rank,
        authority="BIS",
        document_type="indian_standard",
    )



# ---------------------------------------------------------------------------
# 1. STRONG EXACT CLAUSE EVIDENCE -> HIGH CONFIDENCE
# ---------------------------------------------------------------------------
def test_strong_exact_clause_evidence_yields_high_confidence():
    ev = [
        _make_evidence(chunk_id="c1", clause_id="4.1", reranker_score=4.0),
        _make_evidence(chunk_id="c2", clause_id="4.1", reranker_score=3.2, content="Supplementary calibration specs."),
    ]
    res = EvidenceEvaluator.evaluate("IS 3055 clause 4.1", ev)
    assert res.decision == Decision.ANSWER
    assert res.level == ConfidenceLevel.HIGH
    assert res.score >= 0.70
    assert not res.verification_required
    assert res.query_state == QueryState.ANSWERABLE


# ---------------------------------------------------------------------------
# 2. STRONG SEMANTIC EVIDENCE -> ANSWERABLE
# ---------------------------------------------------------------------------
def test_strong_semantic_evidence_is_answerable():
    ev = [
        _make_evidence(
            chunk_id="c1",
            standard_number="IS 3055",
            clause_id=None,
            reranker_score=3.8,
            content="Calibration procedures and thermometer accuracy tolerance requirements."
        ),
        _make_evidence(
            chunk_id="c2",
            standard_number="IS 3055",
            clause_id=None,
            reranker_score=3.1,
            content="Testing equipment and inspection protocols for clinical accuracy."
        ),
    ]
    res = EvidenceEvaluator.evaluate("calibration accuracy requirements", ev)
    assert res.decision in (Decision.ANSWER, Decision.QUALIFIED_ANSWER)
    assert res.query_state == QueryState.ANSWERABLE
    assert not res.verification_required


# ---------------------------------------------------------------------------
# 3. WEAK SEMANTIC EVIDENCE -> LOWER CONFIDENCE
# ---------------------------------------------------------------------------
def test_weak_semantic_evidence_yields_lower_confidence():
    ev = [
        _make_evidence(
            chunk_id="c1",
            standard_number=None,
            clause_id=None,
            reranker_score=-1.8,
            content="General historical introduction to glassware packaging and shipping crates."
        )
    ]
    res = EvidenceEvaluator.evaluate("microbiological incubation tolerance", ev)
    assert res.level in (ConfidenceLevel.LOW, ConfidenceLevel.MEDIUM)
    assert res.score < 0.70


# ---------------------------------------------------------------------------
# 4. NO RELEVANT EVIDENCE -> VERIFICATION REQUIRED
# ---------------------------------------------------------------------------
def test_no_evidence_yields_verification_required():
    res = EvidenceEvaluator.evaluate("IS 3055 clause 4.1", [])
    assert res.decision == Decision.VERIFICATION_REQUIRED
    assert res.level == ConfidenceLevel.LOW
    assert res.score == 0.0
    assert res.verification_required is True
    assert res.query_state == QueryState.INSUFFICIENT_EVIDENCE
    assert res.verification_reason is not None
    assert len(res.required_information) > 0


# ---------------------------------------------------------------------------
# 5. CONFLICTING EVIDENCE -> VERIFICATION REQUIRED
# ---------------------------------------------------------------------------
def test_conflicting_evidence_yields_verification_required():
    ev = [
        _make_evidence(
            chunk_id="c1",
            clause_id="4.1",
            content="The test shall be mandatory and complied with for all batches.",
        ),
        _make_evidence(
            chunk_id="c2",
            clause_id="4.1",
            content="The test shall not be conducted; testing is strictly prohibited.",
        ),
    ]
    res = EvidenceEvaluator.evaluate("IS 3055 clause 4.1", ev)
    assert res.decision == Decision.VERIFICATION_REQUIRED
    assert res.query_state == QueryState.CONFLICTING_EVIDENCE
    assert res.conflicting_evidence_count >= 2
    assert "contradictory" in res.reasons[0].lower() or "conflict" in res.reasons[0].lower()


# ---------------------------------------------------------------------------
# 6. AMBIGUOUS QUERY -> VERIFICATION REQUIRED
# ---------------------------------------------------------------------------
def test_ambiguous_query_yields_verification_required():
    ev = [_make_evidence(chunk_id="c1", reranker_score=1.5)]
    res = EvidenceEvaluator.evaluate("rule", ev)
    assert res.decision == Decision.VERIFICATION_REQUIRED
    assert res.query_state == QueryState.AMBIGUOUS_QUERY
    assert "ambiguous" in res.reasons[0].lower() or "underspecified" in res.reasons[0].lower()


# ---------------------------------------------------------------------------
# 7. EXACT STANDARD MISMATCH -> VERIFICATION REQUIRED
# ---------------------------------------------------------------------------
def test_exact_standard_mismatch_yields_verification_required():
    # User asks for IS 9999, but retrieved chunks are from IS 3055
    ev = [_make_evidence(chunk_id="c1", standard_number="IS 3055")]
    res = EvidenceEvaluator.evaluate("IS 9999 clause 2", ev)
    assert res.decision == Decision.VERIFICATION_REQUIRED
    assert res.query_state == QueryState.INSUFFICIENT_EVIDENCE
    assert "IS 9999" in res.verification_reason


# ---------------------------------------------------------------------------
# 8. EXACT VERSION MISMATCH -> LOWER CONFIDENCE
# ---------------------------------------------------------------------------
def test_exact_version_mismatch_reduces_confidence():
    # User asks for year 2010, retrieved chunk is 2024
    ev = [_make_evidence(chunk_id="c1", standard_year=2024, reranker_score=2.0)]
    res = EvidenceEvaluator.evaluate("IS 3055:2010", ev)
    # The score should reflect lack of version match
    assert res.score < 0.70


# ---------------------------------------------------------------------------
# 9. INCOMPLETE PROVENANCE -> REDUCED CONFIDENCE
# ---------------------------------------------------------------------------
def test_incomplete_provenance_reduces_confidence():
    complete = [_make_evidence(chunk_id="c1", provenance_completeness=1.0, reranker_score=2.5)]
    incomplete = [_make_evidence(chunk_id="c1", provenance_completeness=0.25, page_start=None, reranker_score=2.5)]
    res_c = EvidenceEvaluator.evaluate("calibration requirements", complete)
    res_i = EvidenceEvaluator.evaluate("calibration requirements", incomplete)
    assert res_c.score > res_i.score


# ---------------------------------------------------------------------------
# 10. MULTIPLE SUPPORTING CHUNKS -> INCREASED EVIDENCE STRENGTH
# ---------------------------------------------------------------------------
def test_multiple_supporting_chunks_increases_evidence_strength():
    one_chunk = [_make_evidence(chunk_id="c1", content="Text A", reranker_score=2.5)]
    multi_chunks = [
        _make_evidence(chunk_id="c1", content="Text A", reranker_score=2.5),
        _make_evidence(chunk_id="c2", content="Text B (distinct)", reranker_score=2.4),
        _make_evidence(chunk_id="c3", content="Text C (distinct)", reranker_score=2.2),
    ]
    res_1 = EvidenceEvaluator.evaluate("calibration requirements", one_chunk)
    res_m = EvidenceEvaluator.evaluate("calibration requirements", multi_chunks)
    assert res_m.score >= res_1.score
    assert res_m.supporting_evidence_count == 3


# ---------------------------------------------------------------------------
# 11. DUPLICATE CHUNKS DO NOT FALSELY INFLATE INDEPENDENT EVIDENCE
# ---------------------------------------------------------------------------
def test_duplicate_chunks_do_not_inflate_independent_evidence():
    identical_content = "Clinical thermometers error shall not exceed 0.1 C."
    ev_duplicates = [
        _make_evidence(chunk_id="c1", content=identical_content, reranker_score=2.5),
        _make_evidence(chunk_id="c2", content=identical_content, reranker_score=2.5),
        _make_evidence(chunk_id="c3", content=identical_content, reranker_score=2.5),
    ]
    res = EvidenceEvaluator.evaluate("calibration requirements", ev_duplicates)
    # The duplicate chunks should be collapsed to 1 independent chunk
    assert res.supporting_evidence_count == 1
    assert res.trace["features"]["duplicate_chunks_detected"] == 2


# ---------------------------------------------------------------------------
# 12. NO EVIDENCE != NEGATIVE EVIDENCE (EPISTEMIC HONESTY)
# ---------------------------------------------------------------------------
def test_no_evidence_is_not_negative_evidence():
    res = EvidenceEvaluator.evaluate("IS 9999", [])
    # Must NOT claim that "no requirements exist" or "standard does not exist"
    assert "no such standard exists" not in res.verification_reason.lower()
    assert "not required" not in res.verification_reason.lower()
    assert "no relevant authoritative documentation was retrieved" in res.verification_reason.lower()


# ---------------------------------------------------------------------------
# 13. STANDARD-LEVEL QUERY -> SATISFIED APPROPRIATELY
# ---------------------------------------------------------------------------
def test_standard_level_query_satisfied_by_title_version_evidence():
    ev = [
        _make_evidence(
            chunk_id="c_title",
            clause_id=None,
            reranker_score=4.2,
            content="IS 3055: Specification for Clinical Thermometers (Third Edition 2024)."
        )
    ]
    res = EvidenceEvaluator.evaluate("IS 3055 2024", ev)
    assert res.decision in (Decision.ANSWER, Decision.QUALIFIED_ANSWER)
    assert res.query_state == QueryState.ANSWERABLE


# ---------------------------------------------------------------------------
# 14. CLAUSE-SPECIFIC QUERY -> HEADER CHUNK ALONE CANNOT PRODUCE HIGH CONFIDENCE
# ---------------------------------------------------------------------------
def test_clause_query_with_only_header_chunk_cannot_produce_high_confidence():
    # User asks for specific Clause 4.1, but chunk is only general title page without clause 4.1
    ev = [
        _make_evidence(
            chunk_id="c_head",
            clause_id=None,
            reranker_score=2.5,
            content="IS 3055: Specification for Clinical Thermometers."
        )
    ]
    res = EvidenceEvaluator.evaluate("IS 3055 clause 4.1", ev)
    assert res.level != ConfidenceLevel.HIGH
    assert res.decision == Decision.VERIFICATION_REQUIRED
    assert "4.1" in res.verification_reason


# ---------------------------------------------------------------------------
# 15. AMENDMENT QUERY -> AMENDMENT EVIDENCE REQUIRED FOR HIGH CONFIDENCE
# ---------------------------------------------------------------------------
def test_amendment_query_requires_amendment_evidence():
    # Query asks for Amendment 1, but chunk has no amendment
    ev_no_amd = [_make_evidence(chunk_id="c1", amendment_number="", reranker_score=2.5)]
    res_no_amd = EvidenceEvaluator.evaluate("IS 3055 amendment 1", ev_no_amd)
    assert res_no_amd.level != ConfidenceLevel.HIGH

    # Chunk carries Amendment 1
    ev_amd = [_make_evidence(chunk_id="c1", amendment_number="1", reranker_score=3.8)]
    res_amd = EvidenceEvaluator.evaluate("IS 3055 amendment 1", ev_amd)
    assert res_amd.score > res_no_amd.score


# ---------------------------------------------------------------------------
# 16. PHASE 4.2 RANKING REMAINS INTACT
# ---------------------------------------------------------------------------
def test_phase_4_2_ranking_remains_intact():
    reranker = Reranker()
    q = "IS 3055 clause 4.1"
    c_head = RetrievalResult(
        chunk_id="c_head",
        standard_number="IS 3055",
        clause_id=None,
        page_start=1,
        content="IS 3055 Clinical thermometers title page.",
        source_content="IS 3055 Clinical thermometers title page.",
        reranker_score=4.0,  # higher surface score
    )
    c_41 = RetrievalResult(
        chunk_id="c_41",
        standard_number="IS 3055",
        clause_id="4.1",
        page_start=3,
        content="Clause 4.1 Calibration and Accuracy error limits.",
        source_content="Clause 4.1 Calibration and Accuracy error limits.",
        reranker_score=2.0,  # lower surface score
    )
    ranked = reranker.rerank(q, [c_head, c_41], top_k=2)
    assert ranked[0].chunk_id == "c_41"
    assert ranked[0].clause_id == "4.1"


# ---------------------------------------------------------------------------
# 17. SOURCE CONTENT REMAINS AUTHORITATIVE
# ---------------------------------------------------------------------------
def test_source_content_remains_authoritative():
    raw_source = "Exact statutory wording: Maximum permissible error is 0.1 C."
    ctx_content = "[Section: Calibration | Page: 3] Exact statutory wording: Maximum permissible error is 0.1 C."
    ev = _make_evidence(source_content=raw_source, contextualized_content=ctx_content)
    assert ev.content == raw_source
    assert ev.source_content == raw_source
    assert ev.contextualized_content == ctx_content


# ---------------------------------------------------------------------------
# 18. CONTEXTUALIZED CONTENT REMAINS RETRIEVAL-ONLY
# ---------------------------------------------------------------------------
def test_contextualized_content_is_separate_from_evidence_content():
    ev = EvidenceItem.from_dict({
        "chunk_id": "c1",
        "document_id": "d1",
        "source_content": "Raw authoritative text.",
        "content": "Raw authoritative text.",
        "contextualized_content": "[AI-Context] Breadcrumb header.",
    })
    # The citable evidence content must be the raw authoritative text
    assert ev.content == "Raw authoritative text."
    assert ev.contextualized_content == "[AI-Context] Breadcrumb header."

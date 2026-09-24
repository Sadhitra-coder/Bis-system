"""
tests/test_contextual_retrieval.py

Phase 5 — Contextual Retrieval Test Suite.

Covers all 16 required test categories from Section 24:
1. Structural context generation
2. Missing metadata handling
3. Version-scoped context
4. Amendment-aware context
5. Authoritative source content unchanged
6. Contextualized content separate
7. Context quality validation
8. Malformed context fallback
9. Deterministic context cache identity
10. Embedding representation selection
11. BM25 representation selection
12. CrossEncoder representation
13. Provenance regression
14. Contextual vs baseline A/B evaluation
15. Multilingual / Unicode preservation
16. Phase 4.2 explicit clause ranking preservation
"""

import time
import pytest
from unittest.mock import MagicMock

from app.models import ChunkMetadata, ContextualChunk, DocumentChunk
from app.rag.contextualizer import (
    generate_structural_context,
    build_contextualized_content,
    validate_context,
    contextualize_chunk,
    compute_context_cache_key,
    clear_context_cache,
)
from app.rag.retriever import (
    HybridRetriever,
    evaluate_retrieval,
)
from app.rag.reranker import Reranker
from tests.test_retrieval import make_mock_retriever, SYNTHETIC_CHUNKS


@pytest.fixture
def bis_retriever():
    return make_mock_retriever(SYNTHETIC_CHUNKS)


# ===========================================================
# 1. STRUCTURAL CONTEXT GENERATION
# ===========================================================

def test_structural_context_generation():
    meta = {
        "standard_number": "IS 3055",
        "standard_year": 2024,
        "edition_or_version": "Third Edition",
        "clause_id": "4.1",
        "clause_title": "Calibration and Accuracy",
        "heading_context": ["4 Requirements", "4.1 Calibration and Accuracy"],
        "page_start": 3,
        "page_end": 3,
    }
    ctx = generate_structural_context(meta)
    assert "Standard: IS 3055:2024" in ctx
    assert "Edition: Third Edition" in ctx
    assert "Clause: 4.1 (Calibration and Accuracy)" in ctx
    assert "Hierarchy: 4 Requirements > 4.1 Calibration and Accuracy" in ctx
    assert "Page: 3" in ctx


# ===========================================================
# 2. MISSING METADATA HANDLING
# ===========================================================

def test_missing_metadata_graceful():
    meta = {
        "chunk_id": "c_bare",
        "content": "Minimal text without standard or clause.",
    }
    ctx = generate_structural_context(meta)
    # Should not error; returns empty or bare prefix
    assert ctx == "" or ctx.startswith("[")


def test_partial_metadata_only_includes_available():
    meta = {
        "standard_number": "IS 1234",
        "page_start": 5,
    }
    ctx = generate_structural_context(meta)
    assert "Standard: IS 1234" in ctx
    assert "Page: 5" in ctx
    assert "Edition:" not in ctx
    assert "Clause:" not in ctx


# ===========================================================
# 3. VERSION-SCOPED CONTEXT
# ===========================================================

def test_version_scoped_context():
    meta_v2 = {"standard_number": "IS 3055", "edition_or_version": "Second Edition", "clause_id": "4"}
    meta_v3 = {"standard_number": "IS 3055", "edition_or_version": "Third Edition", "clause_id": "4"}
    ctx_v2 = generate_structural_context(meta_v2)
    ctx_v3 = generate_structural_context(meta_v3)
    assert "Edition: Second Edition" in ctx_v2
    assert "Edition: Third Edition" in ctx_v3
    assert ctx_v2 != ctx_v3


# ===========================================================
# 4. AMENDMENT-AWARE CONTEXT
# ===========================================================

def test_amendment_aware_context():
    meta = {
        "standard_number": "IS 3055",
        "standard_year": 2024,
        "amendment_number": "1",
        "clause_id": "4.1",
        "page_start": 1,
    }
    ctx = generate_structural_context(meta)
    assert "Amendment: Amendment 1" in ctx
    assert "Clause: 4.1" in ctx


# ===========================================================
# 5 & 6. SOURCE CONTENT UNCHANGED & CONTEXT SEPARATE
# ===========================================================

def test_source_content_unchanged_and_separate():
    original = "The permissible error shall not exceed plus or minus 0.1 degree Celsius."
    meta = {
        "chunk_id": "c1",
        "document_id": "doc1",
        "source_hash": "hash123",
        "content": original,
        "standard_number": "IS 3055",
        "clause_id": "4.1",
    }
    chunk = contextualize_chunk(meta)
    # 5. source_content must be strictly identical to original
    assert chunk.source_content == original
    # 6. contextualized_content contains the prefix AND source content
    assert chunk.contextualized_content != original
    assert chunk.contextualized_content.endswith(original)
    assert "Standard: IS 3055" in chunk.contextualized_content
    assert chunk.context_generation_method == "structural"


# ===========================================================
# 7. CONTEXT QUALITY VALIDATION
# ===========================================================

def test_context_validation_success():
    meta = {"standard_number": "IS 3055", "clause_id": "4.1"}
    valid_text = "[Standard: IS 3055 | Clause 4.1 Calibration]"
    is_valid, issues = validate_context(valid_text, meta)
    assert is_valid is True
    assert len(issues) == 0


def test_context_validation_contradictory_standard():
    meta = {"standard_number": "IS 3055"}
    bad_text = "[Standard: IS 8888 | Requirements]"
    is_valid, issues = validate_context(bad_text, meta)
    assert is_valid is False
    assert any("contradictory_standard_number" in i for i in issues)


def test_context_validation_contradictory_clause():
    meta = {"clause_id": "4.1"}
    bad_text = "[Clause 5 Sampling Procedure]"
    is_valid, issues = validate_context(bad_text, meta)
    assert is_valid is False
    assert any("contradictory_clause" in i for i in issues)


# ===========================================================
# 8. MALFORMED / GENERATED CONTEXT FALLBACK
# ===========================================================

def test_malformed_context_fallback():
    # Mock LLM client returning contradictory standard.
    #
    # NOTE ON THE METHOD NAME: this previously stubbed `.generate`, a method
    # OpenAIClient does not have. MagicMock answers any attribute, so the test
    # passed while production raised AttributeError on every real call. The
    # stub must match the real client contract — `chat_completion` — or the
    # test proves nothing about the code that actually runs.
    mock_llm = MagicMock()
    mock_llm.chat_completion.return_value = (
        "Contradictory summary for IS 9999 clause 10"
    )

    meta = {
        "chunk_id": "c_fail",
        "source_hash": "hash_fail",
        "content": "Real thermometer text.",
        "standard_number": "IS 3055",
        "clause_id": "4.1",
    }
    # With LLM enabled, validation failure must cause fallback to structural
    from app.config import settings
    orig_llm = settings.ENABLE_LLM_CONTEXTUALIZATION
    try:
        settings.ENABLE_LLM_CONTEXTUALIZATION = True
        clear_context_cache()
        chunk = contextualize_chunk(meta, method="llm", llm_client=mock_llm)
        # Must fall back to structural context because LLM text contradicted IS 3055
        assert chunk.context_generation_method == "structural"
        assert "Standard: IS 3055" in chunk.contextualized_content
        assert "IS 9999" not in chunk.contextualized_content
    finally:
        settings.ENABLE_LLM_CONTEXTUALIZATION = orig_llm
        clear_context_cache()


# ===========================================================
# 8b. LLM CONTEXTUALIZATION AGAINST THE REAL CLIENT CONTRACT
#     (Phase 6 section 19)
# ===========================================================

class StrictOpenAIClientStub:
    """
    A stand-in for OpenAIClient that exposes ONLY the real interface.

    Deliberately not a MagicMock. A MagicMock answers every attribute, so it
    satisfied `client.generate(prompt)` — a method OpenAIClient has never had —
    and the entire LLM contextualization path was dead in production while
    the suite stayed green. Any call to a method the real client lacks must
    raise AttributeError here, which is the whole point of the stub.

    The signature mirrors app.llm_client.OpenAIClient.chat_completion exactly,
    so a drift in either direction becomes a TypeError.
    """

    def __init__(self, reply: str = "Specifies calibration accuracy limits."):
        self.reply = reply
        self.calls = []

    def chat_completion(
        self,
        messages,
        model,
        temperature=0.0,
        max_completion_tokens=None,
        response_format=None,
        description="completion",
    ) -> str:
        self.calls.append({
            "messages": messages,
            "model": model,
            "temperature": temperature,
            "max_completion_tokens": max_completion_tokens,
            "response_format": response_format,
            "description": description,
        })
        return self.reply


def _llm_enabled(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "ENABLE_LLM_CONTEXTUALIZATION", True)
    clear_context_cache()


def test_llm_contextualization_calls_the_real_client_method(monkeypatch):
    """
    The section 19 regression: production must call chat_completion.

    With a strict stub, the old `client.generate(prompt)` raises
    AttributeError, the broad except swallows it, and the method falls back
    to structural — so asserting method == "llm" is exactly the assertion
    the old code could not satisfy.
    """
    _llm_enabled(monkeypatch)
    client = StrictOpenAIClientStub("Sets permissible error limits for thermometers.")

    meta = {
        "chunk_id": "c_llm_ok",
        "document_id": "doc_llm",
        "source_hash": "hash_llm",
        "content": "The permissible error shall not exceed 0.1 degree Celsius.",
        "standard_number": "IS 3055",
        "standard_year": 2024,
        "clause_id": "4.1",
    }
    chunk = contextualize_chunk(meta, method="llm", llm_client=client)

    assert len(client.calls) == 1, "chat_completion was not the method invoked"
    assert chunk.context_generation_method == "llm"
    assert "AI-Generated Context" in chunk.contextualized_content
    assert "permissible error limits" in chunk.contextualized_content
    # Authoritative text is never rewritten by the generated preface.
    assert chunk.source_content == meta["content"]
    assert chunk.contextualized_content.endswith(meta["content"])
    clear_context_cache()


def test_llm_prompt_is_a_two_turn_chat_with_bounded_input(monkeypatch):
    _llm_enabled(monkeypatch)
    from app.config import settings

    client = StrictOpenAIClientStub()
    long_body = "x" * (settings.LLM_CONTEXT_INPUT_CHARS + 5000)
    contextualize_chunk(
        {
            "chunk_id": "c_llm_bounds",
            "source_hash": "hash_bounds",
            "content": long_body,
            "standard_number": "IS 3055",
        },
        method="llm",
        llm_client=client,
    )

    call = client.calls[0]
    roles = [m["role"] for m in call["messages"]]
    assert roles == ["system", "user"]
    assert call["temperature"] == 0.0
    assert call["max_completion_tokens"] == settings.LLM_CONTEXT_MAX_TOKENS
    assert call["model"] == settings.OPENAI_MODEL
    # The passage is truncated: one preface does not need the whole chunk,
    # and an unbounded prompt is an unbounded bill.
    assert len(call["messages"][1]["content"]) < len(long_body)
    clear_context_cache()


def test_llm_prompt_never_asserts_an_unknown_standard(monkeypatch):
    """
    The prompt must not fabricate the premise it forbids the model to invent.

    The old implementation defaulted standard_number to the literal string
    "Unknown Standard" and then told the model the passage came from "Indian
    Standard Unknown Standard".
    """
    _llm_enabled(monkeypatch)
    client = StrictOpenAIClientStub()
    contextualize_chunk(
        {
            "chunk_id": "c_llm_bare",
            "source_hash": "hash_bare",
            "content": "Unattributed technical text.",
        },
        method="llm",
        llm_client=client,
    )

    user_turn = client.calls[0]["messages"][1]["content"]
    assert "Unknown Standard" not in user_turn
    assert "Standard:" not in user_turn
    assert "No document identity is available." in user_turn
    clear_context_cache()


def test_llm_failure_falls_back_without_breaking_ingestion(monkeypatch):
    _llm_enabled(monkeypatch)

    class ExplodingClient:
        def chat_completion(self, **kwargs):
            raise RuntimeError("service unavailable")

    chunk = contextualize_chunk(
        {
            "chunk_id": "c_llm_boom",
            "source_hash": "hash_boom",
            "content": "Real text survives an outage.",
            "standard_number": "IS 3055",
            "clause_id": "4.1",
        },
        method="llm",
        llm_client=ExplodingClient(),
    )
    assert chunk.context_generation_method == "structural"
    assert "Standard: IS 3055" in chunk.contextualized_content
    assert chunk.source_content == "Real text survives an outage."
    clear_context_cache()


def test_llm_empty_reply_falls_back_to_structural(monkeypatch):
    _llm_enabled(monkeypatch)
    chunk = contextualize_chunk(
        {
            "chunk_id": "c_llm_empty",
            "source_hash": "hash_empty",
            "content": "Text with an empty model reply.",
            "standard_number": "IS 3055",
        },
        method="llm",
        llm_client=StrictOpenAIClientStub("   "),
    )
    assert chunk.context_generation_method == "structural"
    clear_context_cache()


def test_llm_path_stays_off_behind_the_feature_flag(monkeypatch):
    """The flag must remain authoritative: no client call when disabled."""
    from app.config import settings
    monkeypatch.setattr(settings, "ENABLE_LLM_CONTEXTUALIZATION", False)
    clear_context_cache()

    client = StrictOpenAIClientStub()
    chunk = contextualize_chunk(
        {
            "chunk_id": "c_llm_off",
            "source_hash": "hash_off",
            "content": "Flag is off.",
            "standard_number": "IS 3055",
        },
        method="llm",
        llm_client=client,
    )
    assert client.calls == []
    assert chunk.context_generation_method == "structural"
    clear_context_cache()


# ===========================================================
# 9. DETERMINISTIC CONTEXT CACHE IDENTITY
# ===========================================================

def test_deterministic_context_cache_identity():
    clear_context_cache()
    meta = {
        "chunk_id": "chunk_deterministic",
        "source_hash": "abc123hash",
        "content": "Stable calibration requirements.",
        "standard_number": "IS 3055",
        "clause_id": "4.1",
    }
    key1 = compute_context_cache_key("abc123hash", "chunk_deterministic")
    key2 = compute_context_cache_key("abc123hash", "chunk_deterministic")
    assert key1 == key2

    c1 = contextualize_chunk(meta)
    c2 = contextualize_chunk(meta)
    assert c1 is c2  # Identical cached object returned
    clear_context_cache()


# ===========================================================
# 9b. CACHE KEY COVERS EVERY CONTEXT INPUT
#     (Phase 6 section 20)
# ===========================================================

def _keyed(**over):
    """A chunk that is identical apart from the supplied overrides."""
    meta = {
        "chunk_id": "chunk_shared",
        "document_id": "doc_shared",
        "source_hash": "doc_hash_shared",
        "content": "Identical passage text appearing twice.",
        "standard_number": "IS 3055",
        "clause_id": "4.1",
        "section": "4.1 Calibration",
        "page_start": 3,
        "page_end": 3,
    }
    meta.update(over)
    return compute_context_cache_key(
        source_hash=meta["source_hash"],
        chunk_id=meta["chunk_id"],
        metadata=meta,
        source_content=meta["content"],
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("section", "7.2 Marking"),
        ("clause_id", "7.2"),
        ("clause_title", "Marking"),
        ("page_start", 42),
        ("page_end", 44),
        ("heading_context", ["7 Marking", "7.2 Marking"]),
        ("standard_number", "IS 456"),
        ("standard_year", 1999),
        ("standard_title", "Something else"),
        ("standard_id", "std_IS_3055_other"),
        ("version_id", "ver_std_IS_3055_other"),
        ("edition_or_version", "Second Edition"),
        ("part_number", "2"),
        ("amendment_number", "1"),
        ("knowledge_clause_id", "cls_std_IS_3055_other"),
        ("authority", "Some other authority"),
    ],
)
def test_cache_key_changes_when_a_context_input_changes(field, value):
    """
    THE BUG THIS PINS
    -----------------
    The key was (source_hash, chunk_id, version, method) — four inputs for a
    context derived from roughly twenty. `source_hash` is the DOCUMENT hash
    and `chunk_id` is content-addressed, so two passages with identical text
    under different headings collided: the second inherited the first one's
    heading, clause and page prefix. Fabricated provenance, produced by the
    cache rather than by any parser.
    """
    assert _keyed() != _keyed(**{field: value})


def test_identical_inputs_still_hit_the_cache():
    """Correctness must not have been bought by disabling reuse."""
    assert _keyed() == _keyed()


def test_cache_key_distinguishes_absent_from_empty():
    """
    JSON, not a delimiter-joined string, precisely so this holds.

    ':'.join(...) cannot express the difference between section=None and
    section='' — and the index writes '' for absent values, so the two arrive
    from genuinely different places.
    """
    assert _keyed(section=None) != _keyed(section="")


def test_cache_key_covers_generation_settings_and_content():
    common = dict(source_hash="h", chunk_id="c", metadata={"section": "1"})
    assert (
        compute_context_cache_key(**common, source_content="alpha")
        != compute_context_cache_key(**common, source_content="beta")
    )
    assert (
        compute_context_cache_key(**common, context_version="1.0")
        != compute_context_cache_key(**common, context_version="2.0")
    )
    assert (
        compute_context_cache_key(**common, method="structural")
        != compute_context_cache_key(**common, method="llm")
    )
    assert (
        compute_context_cache_key(**common, max_chars=400)
        != compute_context_cache_key(**common, max_chars=200)
    )
    assert (
        compute_context_cache_key(**common, llm_model="model-a")
        != compute_context_cache_key(**common, llm_model="model-b")
    )


def test_colliding_chunk_ids_do_not_share_a_context():
    """
    End-to-end form of the same defect: identical text under two headings.

    Content-addressed ids make this collision the normal case, not an edge
    case — a boilerplate sentence repeated in two clauses hashes the same.
    """
    clear_context_cache()
    shared = {
        "chunk_id": "chunk_boilerplate",
        "document_id": "doc_collide",
        "source_hash": "hash_collide",
        "content": "Conformity shall be established by test.",
        "standard_number": "IS 3055",
    }
    first = contextualize_chunk({**shared, "clause_id": "4.1", "page_start": 3})
    second = contextualize_chunk({**shared, "clause_id": "9.2", "page_start": 17})

    assert first is not second
    assert "Clause: 4.1" in first.contextualized_content
    assert "Page: 3" in first.contextualized_content
    assert "Clause: 9.2" in second.contextualized_content
    assert "Page: 17" in second.contextualized_content
    # The second passage must not inherit the first one's provenance.
    assert "Clause: 4.1" not in second.contextualized_content
    assert "Page: 3" not in second.contextualized_content
    clear_context_cache()


# ===========================================================
# 9c. UNKNOWN SENTINEL TOLERANCE ON THE READ PATH
# ===========================================================

def test_unknown_int_sentinel_is_not_rendered_as_a_page_or_year():
    """
    A chunk round-tripped through Chroma carries UNKNOWN_INT (-1), because
    the index cannot store null and 0 is a real page. 'Standard: IS 3055:-1'
    and 'Page: -1' would be fabricated provenance.
    """
    from app.index_schema import UNKNOWN_INT

    ctx = generate_structural_context({
        "standard_number": "IS 3055",
        "standard_year": UNKNOWN_INT,
        "page_start": UNKNOWN_INT,
        "page_end": UNKNOWN_INT,
    })
    assert "Standard: IS 3055" in ctx
    assert "-1" not in ctx
    assert "Page" not in ctx


def test_page_zero_is_kept_because_unknown_is_minus_one():
    ctx = generate_structural_context({"standard_number": "IS 3055", "page_start": 0})
    assert "Page: 0" in ctx


def test_contextual_chunk_decodes_sentinels_and_carries_standard_id():
    from app.index_schema import UNKNOWN_INT

    clear_context_cache()
    chunk = contextualize_chunk({
        "chunk_id": "c_sentinel",
        "document_id": "doc_sentinel",
        "source_hash": "hash_sentinel",
        "content": "Text with unknown provenance.",
        "standard_number": "IS 3055",
        "standard_year": UNKNOWN_INT,
        "page_start": UNKNOWN_INT,
        "page_end": UNKNOWN_INT,
        "standard_id": "std_IS_3055_abc123",
        "version_id": "ver_std_IS_3055_abc123_def456",
    })
    assert chunk.standard_year is None
    assert chunk.page_start is None
    assert chunk.page_end is None
    # The join fields must survive contextualization, or the knowledge <->
    # retrieval join cannot be made from a contextualized chunk.
    assert chunk.standard_id == "std_IS_3055_abc123"
    assert chunk.version_id == "ver_std_IS_3055_abc123_def456"
    clear_context_cache()


# ===========================================================
# 10 & 11. EMBEDDING & BM25 REPRESENTATION
# ===========================================================

def test_embedding_and_bm25_representations():
    """Verify prepare_chunks stores contextualized_content in documents and source_content in metadata."""
    from app.steps.embed import prepare_chunks
    chunks = [
        {
            "chunk_id": "c_test",
            "content": "The permissible error shall not exceed 0.1 degree.",
            "document_id": "doc1",
            "source_file": "IS_3055.pdf",
            "standard_number": "IS 3055",
            "standard_year": 2024,
            "clause_id": "4.1",
            "page_start": 3,
        }
    ]
    ids, documents, metadatas = prepare_chunks(chunks)
    assert len(documents) == 1
    # Document contains contextual prefix for embedding & BM25
    assert "Standard: IS 3055:2024" in documents[0]
    assert "permissible error" in documents[0]
    # Metadata strictly preserves unaltered source_content
    meta = metadatas[0]
    assert meta["source_content"] == "The permissible error shall not exceed 0.1 degree."
    assert "Standard: IS 3055" not in meta["source_content"]
    assert meta["contextualized_content"] == documents[0]


# ===========================================================
# 12. CROSSENCODER REPRESENTATION
# ===========================================================

def test_crossencoder_receives_contextualized_content():
    """Reranker prepares pairs using contextualized_content when present."""
    reranker = Reranker()
    candidate = {
        "chunk_id": "c1",
        "content": "Short text.",
        "source_content": "Short text.",
        "contextualized_content": "[Standard: IS 3055 | Clause: 4.1]\n\nShort text.",
    }
    pairs, valid = reranker._prepare_pairs("IS 3055 calibration", [candidate])
    assert len(pairs) == 1
    # Scored text contains the contextualized representation
    assert "[Standard: IS 3055 | Clause: 4.1]" in pairs[0][1]


# ===========================================================
# 13. PROVENANCE REGRESSION
# ===========================================================

def test_provenance_preserved_with_contextual_chunks():
    chunks = [
        {
            "chunk_id": "chunk_prov_c41",
            "content": "Clinical thermometers calibration.",
            "metadata": {
                "document_id": "doc_prov_1",
                "source_hash": "hash_prov_abc",
                "source_file": "IS_3055.pdf",
                "page_start": 3,
                "page_end": 3,
                "standard_number": "IS 3055",
                "clause_id": "4.1",
                "source_content": "Clinical thermometers calibration.",
                "contextualized_content": "[Standard: IS 3055 | Clause: 4.1]\n\nClinical thermometers calibration.",
            },
        }
    ]
    retriever = make_mock_retriever(chunks)
    results = retriever.retrieve("IS 3055 clause 4.1", top_k=1)
    assert len(results) == 1
    r = results[0]
    assert r.chunk_id == "chunk_prov_c41"
    assert r.document_id == "doc_prov_1"
    assert r.source_hash == "hash_prov_abc"
    assert r.source_file == "IS_3055.pdf"
    assert r.page_start == 3
    assert r.page_end == 3
    assert r.clause_id == "4.1"
    assert r.content == "Clinical thermometers calibration."  # authoritative
    assert r.source_content == "Clinical thermometers calibration."
    assert "Standard: IS 3055" in r.contextualized_content


# ===========================================================
# 14. CONTEXTUAL VS BASELINE A/B EVALUATION
# ===========================================================

def test_contextual_vs_baseline_ab_evaluation():
    """
    A/B evaluation comparing:
    Baseline: raw uncontextualized chunks
    Experiment: contextualized chunks
    Over queries where context matters.
    """
    # 1. Baseline uncontextualized corpus
    baseline_chunks = [
        {
            "chunk_id": c["chunk_id"],
            "content": c["content"],  # uncontextualized
            "metadata": dict(c["metadata"]),
        }
        for c in SYNTHETIC_CHUNKS
    ]
    baseline_retriever = make_mock_retriever(baseline_chunks)

    # 2. Contextualized corpus
    contextual_chunks = []
    for c in SYNTHETIC_CHUNKS:
        ctx = contextualize_chunk(c)
        meta = dict(c["metadata"])
        meta["source_content"] = ctx.source_content
        meta["contextualized_content"] = ctx.contextualized_content
        contextual_chunks.append({
            "chunk_id": c["chunk_id"],
            "content": ctx.contextualized_content,  # indexed with context
            "metadata": meta,
        })
    contextual_retriever = make_mock_retriever(contextual_chunks)

    eval_dataset = [
        {"query": "What are the calibration accuracy requirements?", "expected_chunk_ids": ["chunk_is3055_clause41"]},
        {"query": "What are the permissible errors?", "expected_chunk_ids": ["chunk_is3055_clause41"]},
        {"query": "What material requirements apply?", "expected_chunk_ids": ["chunk_is3055_clause4"]},
        {"query": "Which clause covers calibration?", "expected_chunk_ids": ["chunk_is3055_clause41"]},
        {"query": "IS 3055 2024 clause 4.1", "expected_chunk_ids": ["chunk_is3055_clause41", "chunk_is3055_amd1"]},
        {"query": "Amendment 1 calibration", "expected_chunk_ids": ["chunk_is3055_amd1"]},
    ]

    base_metrics = evaluate_retrieval(baseline_retriever, eval_dataset, k_values=[1, 3, 5])
    ctx_metrics = evaluate_retrieval(contextual_retriever, eval_dataset, k_values=[1, 3, 5])

    assert "Recall@1" in ctx_metrics
    assert "MRR@5" in ctx_metrics
    # Contextual retrieval should match or improve baseline metrics
    assert ctx_metrics["Recall@3"] >= base_metrics["Recall@3"]
    assert ctx_metrics["Recall@5"] >= 0.80


# ===========================================================
# 15. MULTILINGUAL / UNICODE PRESERVATION
# ===========================================================

def test_multilingual_unicode_preservation_in_context():
    meta = {
        "standard_number": "IS 3055",
        "clause_id": "4.1",
        "clause_title": "गुणवत्ता और सटीकता",
        "section": "मानक विनिर्देश",
        "page_start": 2,
    }
    ctx = generate_structural_context(meta)
    assert "गुणवत्ता और सटीकता" in ctx
    assert "Standard: IS 3055" in ctx


# ===========================================================
# 16. PHASE 4.2 EXPLICIT CLAUSE RANKING PRESERVATION
# ===========================================================

def test_phase42_explicit_clause_ranking_still_works(bis_retriever):
    """Verify that intent-aware ranking still ensures exact clause wins."""
    reranker = Reranker()
    query = "IS 3055 clause 4.1"
    retrieved = bis_retriever.retrieve(query, top_k=4)
    reranked = reranker.rerank(query=query, results=retrieved, top_k=4)
    top = reranked[0]
    assert top.clause_id == "4.1"
    assert top.standard_number == "IS 3055"
    assert "exact_clause_match:4.1" in (top.ranking_reason or "")

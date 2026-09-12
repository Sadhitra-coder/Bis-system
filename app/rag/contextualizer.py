"""
app/rag/contextualizer.py

Phase 5 — Contextual Retrieval Engine.

Responsible for generating, caching, and validating contextual representations
for indexed chunks, strictly separating authoritative source_content from
retrieval-aid contextualized_content.

Modes:
A. STRUCTURAL CONTEXT (default, deterministic from verified metadata)
B. LLM-GENERATED CONTEXT (optional, feature-flagged, fallback to structural)

Guarantees:
- source_content remains authoritative, unaltered, and traceable.
- Context never fabricates standards, clauses, dates, or legal applicability.
- Contradictory generated context is rejected via validation and falls back to structural.
- Ingestion-time caching avoids redundant context generation.
"""

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from app.config import settings
from app.index_schema import decode_optional_int
from app.models import ChunkMetadata, ContextualChunk, DocumentChunk

logger = logging.getLogger(__name__)

# In-memory context cache for deterministic reuse during ingestion runs
# Key: compute_context_cache_key(...) -> ContextualChunk
_CONTEXT_CACHE: Dict[str, ContextualChunk] = {}


# ============================================================
# CACHE KEY GENERATION
# ============================================================

#: Metadata fields that participate in context generation.
#:
#: Every field whose value can change the produced context MUST appear here.
#: The identity fields (standard_id, version_id, knowledge_clause_id) are
#: included even where the current structural builder does not print them,
#: because a chunk reassigned to a different StandardVersion has a different
#: context identity even when its printable fields are unchanged.
CONTEXT_INPUT_FIELDS: Tuple[str, ...] = (
    # Standard identity
    "standard_number",
    "standard_title",
    "standard_year",
    "standard_id",
    "authority",
    # Version / edition identity
    "edition_or_version",
    "version_id",
    "part_number",
    "part",
    # Amendment identity
    "amendment_number",
    # Structural position
    "section",
    "heading_context",
    "clause_id",
    "clause_title",
    "knowledge_clause_id",
    # Page provenance
    "page_start",
    "page_end",
    "page_number",
)


def compute_context_cache_key(
    source_hash: str,
    chunk_id: str,
    context_version: Optional[str] = None,
    method: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    source_content: Optional[str] = None,
    max_chars: Optional[int] = None,
    llm_model: Optional[str] = None,
) -> str:
    """
    Deterministic cache identity for chunk contextualization.

    WHY THIS IS NOT JUST (source_hash, chunk_id, version, method)
    ------------------------------------------------------------
    The previous key covered four inputs while the generated context is
    derived from roughly twenty. Two concrete failures followed:

      * `source_hash` is the DOCUMENT hash. Two chunks differing only in
        section, clause or page produced different context but hashed the
        same on every term except `chunk_id`, so any construction that
        reused a chunk id — a re-ingest after a metadata correction, for
        instance — served the superseded context for the rest of the run.
      * `chunk_id` is content-addressed, so identical text appearing under
        two different headings collides. The second occurrence inherited
        the first one's heading, clause and page prefix: fabricated
        provenance, produced by the cache rather than by any parser.

    Every input that can change the output is therefore represented. The
    payload is JSON so that absent (null) stays distinguishable from empty
    (""), which a delimiter-joined string cannot express.

    Parameters not supplied fall back to the live settings values, read at
    call time rather than at import time.
    """
    payload: Dict[str, Any] = {
        "source_hash": source_hash,
        "chunk_id": chunk_id,
        "context_version": (
            context_version
            if context_version is not None
            else settings.CONTEXT_GENERATION_VERSION
        ),
        "method": (
            method if method is not None else settings.CONTEXT_GENERATION_METHOD
        ),
        # Truncation length changes the stored prefix.
        "max_chars": (
            max_chars if max_chars is not None else settings.MAX_CONTEXT_CHARS
        ),
        # Only meaningful in llm mode; null keeps structural keys stable.
        "llm_model": llm_model,
    }

    meta = metadata or {}
    for field in CONTEXT_INPUT_FIELDS:
        payload[field] = meta.get(field)

    # The LLM prompt embeds the passage text, and a content-addressed
    # chunk_id is not guaranteed by every ingestion path, so bind the
    # content explicitly rather than assume the id covers it.
    if source_content is not None:
        payload["content_digest"] = hashlib.sha256(
            source_content.encode("utf-8")
        ).hexdigest()

    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def clear_context_cache() -> None:
    """Clear in-memory context cache (primarily for tests)."""
    global _CONTEXT_CACHE
    _CONTEXT_CACHE.clear()


# ============================================================
# METADATA EXTRACTION HELPER
# ============================================================

def _extract_meta_dict(chunk_or_meta: Union[Dict[str, Any], ChunkMetadata, DocumentChunk]) -> Dict[str, Any]:
    """Extract a flat metadata dictionary from any chunk or metadata object."""
    if isinstance(chunk_or_meta, ChunkMetadata):
        return chunk_or_meta.model_dump()
    if isinstance(chunk_or_meta, DocumentChunk):
        base = chunk_or_meta.metadata.model_dump()
        base["content"] = chunk_or_meta.content
        base["chunk_id"] = chunk_or_meta.chunk_id
        return base
    if isinstance(chunk_or_meta, dict):
        meta = chunk_or_meta.get("metadata")
        if isinstance(meta, dict):
            merged = dict(meta)
            merged.setdefault("chunk_id", chunk_or_meta.get("chunk_id"))
            merged.setdefault("content", chunk_or_meta.get("content"))
            return merged
        return dict(chunk_or_meta)
    return {}


# ============================================================
# STRUCTURAL CONTEXT BUILDER (DEFAULT)
# ============================================================

def generate_structural_context(
    chunk_or_meta: Union[Dict[str, Any], ChunkMetadata, DocumentChunk],
    max_chars: int = settings.MAX_CONTEXT_CHARS,
) -> str:
    """
    Build structural context deterministically from verified document metadata.
    Never fabricates missing information; only includes non-empty fields.

    Example output:
    [Standard: IS 3055:2024 | Edition: Third Edition | Section: 4 Requirements > 4.1 Calibration | Clause: 4.1 | Page: 3]
    """
    meta = _extract_meta_dict(chunk_or_meta)

    parts = []

    # 1. Authority & Standard
    #
    # standard_year is decoded rather than read raw: a chunk round-tripped
    # through the index carries UNKNOWN_INT (-1) for an unknown year, and
    # "Standard: IS 3055:-1" would be fabricated provenance.
    std = meta.get("standard_number")
    year = decode_optional_int(meta.get("standard_year"))
    if std:
        if year is not None:
            parts.append(f"Standard: {std}:{year}")
        else:
            parts.append(f"Standard: {std}")
    elif meta.get("authority"):
        parts.append(f"Authority: {meta['authority']}")

    # 2. Version / Edition
    edition = meta.get("edition_or_version")
    if edition:
        parts.append(f"Edition: {edition}")

    # 3. Amendment
    amd = meta.get("amendment_number")
    if amd:
        parts.append(f"Amendment: Amendment {amd}")

    # 4. Part
    part = meta.get("part_number") or meta.get("part")
    if part:
        parts.append(f"Part: {part}")

    # 5. Section / Hierarchy
    hc = meta.get("heading_context")
    if hc and isinstance(hc, list) and len(hc) > 0:
        clean_hc = [str(h).strip() for h in hc if str(h).strip()]
        if clean_hc:
            parts.append(f"Hierarchy: {' > '.join(clean_hc)}")
    elif meta.get("section"):
        parts.append(f"Section: {meta['section']}")

    # 6. Clause
    clause_id = meta.get("clause_id")
    clause_title = meta.get("clause_title")
    if clause_id:
        if clause_title and clause_title.lower() != clause_id.lower():
            parts.append(f"Clause: {clause_id} ({clause_title})")
        else:
            parts.append(f"Clause: {clause_id}")

    # 7. Page
    #
    # Decoded through the index codec so UNKNOWN_INT (-1) suppresses the
    # page entirely. Page 0 is NOT treated as unknown: the index stores an
    # unknown page as -1 precisely so that 0 can stay a real value, and
    # collapsing the two here would reintroduce the ambiguity.
    page_start = decode_optional_int(meta.get("page_start"))
    if page_start is None:
        page_start = decode_optional_int(meta.get("page_number"))
    page_end = decode_optional_int(meta.get("page_end"))

    if page_start is not None:
        p_end = page_end if page_end is not None else page_start
        if page_start == p_end:
            parts.append(f"Page: {page_start}")
        else:
            parts.append(f"Pages: {page_start}-{p_end}")

    if not parts:
        return ""

    prefix = "[" + " | ".join(parts) + "]"
    if len(prefix) > max_chars:
        prefix = prefix[:max_chars - 3] + "...]"
    return prefix


# ============================================================
# LLM CONTEXT BUILDER (OPTIONAL)
# ============================================================

#: System instruction for the optional LLM context path. Kept separate from
#: the user turn so the constraint survives a long passage in the user
#: message, which is where instruction-following degrades first.
_LLM_CONTEXT_SYSTEM_PROMPT = (
    "You write one-sentence retrieval prefaces for passages from technical "
    "and regulatory documents. State only the technical topic the passage "
    "addresses. Never invent standard numbers, clause numbers, dates, "
    "quantities, or legal obligations. Never state a requirement the passage "
    "does not contain. Maximum 30 words. Reply with the sentence only."
)


def _describe_known_identity(metadata: Dict[str, Any]) -> str:
    """
    Identity lines for the LLM prompt, naming only what is actually known.

    The previous implementation defaulted standard_number to the literal
    string "Unknown Standard" and then asserted the passage came from
    "Indian Standard Unknown Standard" — a fabricated premise handed to the
    model in the very prompt that forbids fabrication. An unknown field is
    now omitted instead.
    """
    lines: List[str] = []

    std = metadata.get("standard_number")
    if std and str(std).strip():
        year = decode_optional_int(metadata.get("standard_year"))
        lines.append(
            f"Standard: {std}:{year}" if year is not None else f"Standard: {std}"
        )

    for label, key in (
        ("Edition", "edition_or_version"),
        ("Clause", "clause_id"),
        ("Section", "section"),
    ):
        value = metadata.get(key)
        if value and str(value).strip():
            lines.append(f"{label}: {value}")

    return "\n".join(lines) if lines else "No document identity is available."


def generate_llm_context(
    source_content: str,
    metadata: Dict[str, Any],
    client: Optional[Any] = None,
    model: Optional[str] = None,
) -> Optional[str]:
    """
    Generate short, factual LLM context when feature-flagged.

    Returns None if the LLM is disabled, no client was supplied, the call
    fails, or the response is empty. The caller then uses structural
    context, so this path can never block ingestion.

    THE BUG THIS FIXES
    ------------------
    The call was `client.generate(prompt)`. GroqClient has no `generate`
    method — its interface is `chat_completion(messages=..., model=...)`.
    Any real client therefore raised AttributeError, which the broad
    `except` below swallowed into a warning, so the feature reported itself
    as "enabled, falling back" on every chunk and was never once exercised.
    A MagicMock in the test suite answered `.generate` happily, which is why
    the suite stayed green.

    `source_content` is read only. The authoritative text is never modified,
    and the returned string is wrapped so that generated prose remains
    visibly distinct from parsed document text.
    """
    if not settings.ENABLE_LLM_CONTEXTUALIZATION or client is None:
        return None

    try:
        identity = _describe_known_identity(metadata)

        user_prompt = (
            "Write the retrieval preface for the passage below.\n\n"
            "KNOWN DOCUMENT IDENTITY\n"
            f"{identity}\n\n"
            "PASSAGE\n"
            f"{source_content[:settings.LLM_CONTEXT_INPUT_CHARS]}"
        )

        response = client.chat_completion(
            messages=[
                {"role": "system", "content": _LLM_CONTEXT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            model=model or settings.GROQ_MODEL,
            temperature=0.0,
            max_completion_tokens=settings.LLM_CONTEXT_MAX_TOKENS,
            description="chunk contextualization",
        )

        text = str(response).strip() if response is not None else ""
        if text:
            return f"[AI-Generated Context: {text}]"

        logger.warning(
            "LLM contextualization returned empty text; using structural context."
        )
    except Exception as exc:
        # Deliberately broad: contextualization is an optional retrieval aid.
        # A model outage, a bad key or a client-contract change must degrade
        # to structural context, never fail an ingestion run.
        logger.warning(
            "LLM contextualization failed (%s: %s), falling back to structural.",
            type(exc).__name__,
            exc,
        )

    return None


# ============================================================
# CONTEXT QUALITY VALIDATION (Prompt 18)
# ============================================================

_IS_CONTRADICTION_PATTERN = re.compile(r'\bIS\s+(\d+)\b', re.IGNORECASE)
_CLAUSE_CONTRADICTION_PATTERN = re.compile(r'\bClause\s+(\d+(?:\.\d+)*)\b', re.IGNORECASE)


def validate_context(
    context_text: str,
    metadata: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    """
    Validates that the generated context does not contradict known metadata.

    Rules:
    1. If standard_number is specified (e.g. 'IS 3055'), context must not mention
       a different standard number (e.g. 'IS 8888').
    2. If clause_id is specified (e.g. '4.1'), context must not assert a conflicting
       non-parent, non-subclause clause number.
    3. If amendment_number is specified, context must not assert a different amendment.

    Returns:
        (is_valid: bool, issues: List[str])
    """
    issues = []
    if not context_text:
        return True, issues

    # 1. Standard number contradiction
    std = metadata.get("standard_number")
    if std:
        m_std = re.search(r'\bIS\s+(\d+)', std, re.IGNORECASE)
        if m_std:
            expected_num = m_std.group(1)
            found_stds = _IS_CONTRADICTION_PATTERN.findall(context_text)
            for found_num in found_stds:
                if found_num != expected_num:
                    issues.append(f"contradictory_standard_number: found IS {found_num} vs expected IS {expected_num}")

    # 2. Clause contradiction
    clause_id = metadata.get("clause_id")
    if clause_id:
        expected_clause = str(clause_id).strip()
        found_clauses = _CLAUSE_CONTRADICTION_PATTERN.findall(context_text)
        for fc in found_clauses:
            # Allow exact match, parent clause (e.g. 4 vs 4.1), or subclause (e.g. 4.1.1 vs 4.1)
            if fc != expected_clause and not expected_clause.startswith(fc + ".") and not fc.startswith(expected_clause + "."):
                issues.append(f"contradictory_clause: found Clause {fc} vs expected Clause {expected_clause}")

    # 3. Amendment contradiction
    amd = metadata.get("amendment_number")
    if amd:
        expected_amd = str(amd).strip()
        found_amds = re.findall(r'\bAmendment\s+(\d+)\b', context_text, re.IGNORECASE)
        for fa in found_amds:
            if fa != expected_amd:
                issues.append(f"contradictory_amendment: found Amendment {fa} vs expected Amendment {expected_amd}")

    return len(issues) == 0, issues


# ============================================================
# CONTENT COMBINER
# ============================================================

def build_contextualized_content(source_content: str, context_prefix: str) -> str:
    """
    Combines verified context prefix and literal source content.
    If context_prefix is empty, returns source_content unaltered.
    """
    if not context_prefix:
        return source_content
    return f"{context_prefix}\n\n{source_content}"


# ============================================================
# PRIMARY CONTEXTUALIZATION PIPELINE
# ============================================================

def contextualize_chunk(
    chunk: Union[Dict[str, Any], DocumentChunk],
    method: Optional[str] = None,
    llm_client: Optional[Any] = None,
    llm_model: Optional[str] = None,
) -> ContextualChunk:
    """
    Canonical contextualization entry point for a chunk.

    1. Checks cache for deterministic reuse.
    2. Builds structural context (or optional LLM context if enabled).
    3. Validates context against metadata; falls back to structural if invalid.
    4. Produces ContextualChunk preserving authoritative source_content.
    """
    meta = _extract_meta_dict(chunk)

    chunk_id = str(meta.get("chunk_id") or "")
    doc_id = str(meta.get("document_id") or "")
    source_hash = str(meta.get("source_hash") or "")
    source_content = str(meta.get("content") or "")

    requested_method = method or settings.CONTEXT_GENERATION_METHOD
    version = settings.CONTEXT_GENERATION_VERSION
    resolved_model = (
        (llm_model or settings.GROQ_MODEL) if requested_method == "llm" else None
    )

    # Check cache. The key covers every metadata field that feeds context
    # generation, so a chunk whose section, clause, page or standard identity
    # changed cannot be served a context built from the previous values.
    cache_key = compute_context_cache_key(
        source_hash=source_hash,
        chunk_id=chunk_id,
        context_version=version,
        method=requested_method,
        metadata=meta,
        source_content=source_content,
        llm_model=resolved_model,
    )
    if cache_key in _CONTEXT_CACHE:
        return _CONTEXT_CACHE[cache_key]

    # Generate context
    context_prefix = ""
    used_method = "structural"

    if requested_method == "llm" and settings.ENABLE_LLM_CONTEXTUALIZATION:
        llm_text = generate_llm_context(
            source_content, meta, client=llm_client, model=resolved_model
        )
        if llm_text:
            is_valid, issues = validate_context(llm_text, meta)
            if is_valid:
                context_prefix = llm_text
                used_method = "llm"
            else:
                logger.warning(
                    "Generated context contradicted metadata for chunk %s (%s); "
                    "using structural context.",
                    chunk_id,
                    "; ".join(issues),
                )

    if not context_prefix:
        # Default structural context
        context_prefix = generate_structural_context(meta)
        used_method = "structural"

    # Assemble contextualized content. source_content is passed through
    # unaltered — the prefix is prepended to the retrieval representation
    # only, never written back over the authoritative text.
    contextualized_content = build_contextualized_content(source_content, context_prefix)

    # Build ContextualChunk
    hc = meta.get("heading_context")
    heading_ctx_list = list(hc) if isinstance(hc, list) else ([str(hc)] if hc else [])

    # Decoded through the index codec: a chunk read back from the index
    # carries -1 for an unknown page or year, and int(-1) would have made
    # that indistinguishable from a real value.
    page_start = decode_optional_int(meta.get("page_start"))
    if page_start is None:
        page_start = decode_optional_int(meta.get("page_number"))
    page_end = decode_optional_int(meta.get("page_end"))
    if page_end is None:
        page_end = page_start

    result = ContextualChunk(
        chunk_id=chunk_id,
        document_id=doc_id,
        source_content=source_content,
        contextualized_content=contextualized_content,
        standard_number=meta.get("standard_number"),
        standard_title=meta.get("standard_title"),
        standard_year=decode_optional_int(meta.get("standard_year")),
        standard_id=meta.get("standard_id"),
        version_id=meta.get("version_id"),
        edition_or_version=meta.get("edition_or_version"),
        part_number=meta.get("part_number") or meta.get("part"),
        section=meta.get("section"),
        heading_context=heading_ctx_list,
        clause_id=meta.get("clause_id"),
        clause_title=meta.get("clause_title"),
        amendment_number=meta.get("amendment_number"),
        page_start=page_start,
        page_end=page_end,
        source_hash=source_hash,
        source_file=meta.get("source_file"),
        context_generation_method=used_method,
        context_generation_version=version,
    )

    # Store in cache
    _CONTEXT_CACHE[cache_key] = result
    return result

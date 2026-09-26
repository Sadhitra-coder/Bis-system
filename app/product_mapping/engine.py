"""
app/product_mapping/engine.py

Core Product-to-Standard Candidate Discovery, Aggregation, and Scoring Engine (Phase 11).

DESIGN PRINCIPLES:
  - Multi-signal scoring: title match, product term match, clause support,
    semantic relevance, technical characteristics match, knowledge graph signals.
  - Title-only match is NOT enough for STRONG_CANDIDATE: content/clause evidence required.
  - Candidates are aggregated at (standard_id, version_id) level with content-hash dedup.
  - Multi-standard products return a deterministic ranked list (Candidate A, B, C).
  - Conservative negative results: returns empty list with VERIFICATION_REQUIRED,
    never claims 'No BIS standard exists' or 'No standard applies'.
  - Decoupled confidence: mapping_score != intent_confidence != evidence_confidence.
  - Zero legal applicability verdicts: strictly candidates.
"""

import hashlib
import logging
import math
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from app.confidence.models import ConfidenceLevel
from app.evidence.models import EvidenceItem
from app.knowledge.models import Standard, StandardStatus
from app.product_mapping.extractor import normalize_product_text
from app.product_mapping.models import (
    MappingReason,
    MappingReasonType,
    MappingStatus,
    ProductContext,
    ProductStandardCandidate,
)

logger = logging.getLogger(__name__)


# ============================================================
# 1. CONTROLLED RETRIEVAL QUERY GENERATION (Section 22)
# ============================================================

def generate_candidate_queries(context: ProductContext) -> List[str]:
    """
    Generates a controlled, non-redundant set of retrieval representations
    from explicit product facts only. Never invents technical characteristics.
    """
    queries: List[str] = []
    seen: Set[str] = set()

    def _add(q: str):
        q_norm = normalize_product_text(q)
        if q_norm and q_norm.lower() not in seen:
            seen.add(q_norm.lower())
            queries.append(q_norm)

    # 1. Primary identifier
    primary = context.primary_identifier
    if primary:
        _add(primary)
        _add(f"{primary} requirements")
        _add(f"{primary} standard")

    # 2. Material + Category / Technology + Category
    cat = context.product_category or context.product_name
    if cat:
        if context.technology:
            _add(f"{context.technology} {cat}")
        if context.material:
            _add(f"{context.material} {cat}")
        if context.intended_use:
            _add(f"{context.intended_use} {cat}")
        if context.technology and context.material:
            _add(f"{context.technology} {context.material} {cat}")

    # 3. Existing standards mentioned
    for std in context.existing_standards:
        _add(std)

    # 4. Fallback to raw query if no structured representation
    if not queries and context.raw_query:
        _add(context.raw_query)

    return queries[:6]


# ============================================================
# 2. EVIDENCE SCORING POLICY (Section 15)
# ============================================================
# Scoring Policy:
#   mapping_score = (
#       w_title   * S_title    (0.25)
#     + w_term    * S_term     (0.25)
#     + w_clause  * S_clause   (0.20)
#     + w_semantic* S_semantic (0.15)
#     + w_tech    * S_tech     (0.10)
#     + w_kg      * S_kg       (0.05)
#   )
#
# Weights sum to 1.0. All component signals are in [0.0, 1.0].
# Documented as evidence-based mapping score, NOT a probability.
# ============================================================

W_TITLE = 0.25
W_TERM = 0.25
W_CLAUSE = 0.20
W_SEMANTIC = 0.15
W_TECH = 0.10
W_KG = 0.05


def _compute_title_match(
    product_context: ProductContext,
    standard_title: Optional[str],
) -> Tuple[float, Optional[str]]:
    """
    Evaluates lexical match between explicit product identifiers and standard title.
    Returns (score in [0.0, 1.0], matched_term).
    """
    if not standard_title:
        return 0.0, None

    title_norm = normalize_product_text(standard_title).lower()
    primary = normalize_product_text(product_context.primary_identifier).lower()

    if not primary:
        return 0.0, None

    # Exact full match or prefix match
    if title_norm.startswith(primary):
        return 1.0, primary
    elif primary in title_norm:
        return 0.90, primary

    # Substring / word overlap match
    p_words = set(w for w in re.findall(r'\b[a-z]{3,}\b', primary) if w not in {"the", "and", "for", "with"})
    t_words = set(w for w in re.findall(r'\b[a-z]{3,}\b', title_norm) if w not in {"the", "and", "for", "with"})

    if not p_words or not t_words:
        return 0.0, None

    intersection = p_words & t_words
    overlap_ratio = len(intersection) / min(len(p_words), len(t_words))

    if overlap_ratio >= 0.66:
        return 0.85, " ".join(sorted(intersection))
    elif overlap_ratio >= 0.33:
        return 0.50, " ".join(sorted(intersection))

    return 0.0, None


def _compute_product_term_match(
    product_context: ProductContext,
    chunks: List[Dict[str, Any]],
) -> Tuple[float, Optional[str], Optional[str]]:
    """
    Evaluates presence of key product terms in chunk content or headings.
    Returns (score, matched_term, evidence_id).
    """
    primary = normalize_product_text(product_context.primary_identifier).lower()
    if not primary:
        return 0.0, None, None

    p_terms = set(w for w in re.findall(r'\b[a-z]{3,}\b', primary) if w not in {"the", "and", "for", "with"})
    if not p_terms:
        return 0.0, None, None

    best_match_count = 0
    best_term = None
    best_chunk_id = None

    for c in chunks:
        cid = c.get("chunk_id") or c.get("metadata", {}).get("chunk_id")
        text = (c.get("content") or c.get("source_content") or "").lower()
        matched = set(t for t in p_terms if t in text)
        if len(matched) > best_match_count:
            best_match_count = len(matched)
            best_term = " ".join(sorted(matched))
            best_chunk_id = cid

    if best_match_count == len(p_terms) and best_match_count > 0:
        return 1.0, best_term, best_chunk_id
    elif best_match_count > 0:
        score = min(1.0, best_match_count / len(p_terms))
        return round(score, 2), best_term, best_chunk_id

    return 0.0, None, None


def _compute_technical_match(
    product_context: ProductContext,
    chunks: List[Dict[str, Any]],
) -> Tuple[float, List[str]]:
    """
    Checks if explicit technical characteristics (material, technology, capacity)
    are corroborated by chunk content.
    """
    explicit_attrs = []
    if product_context.material:
        explicit_attrs.append(product_context.material.lower())
    if product_context.technology:
        explicit_attrs.append(product_context.technology.lower())
    if product_context.intended_use:
        explicit_attrs.append(product_context.intended_use.lower())

    if not explicit_attrs:
        return 0.0, []

    matched_attrs = set()
    for c in chunks:
        text = (c.get("content") or c.get("source_content") or "").lower()
        for attr in explicit_attrs:
            if attr in text:
                matched_attrs.add(attr)

    if not matched_attrs:
        return 0.0, []

    score = len(matched_attrs) / len(explicit_attrs)
    return min(1.0, round(score, 2)), sorted(matched_attrs)


def _compute_clause_support(
    chunks: List[Dict[str, Any]],
) -> Tuple[float, List[str]]:
    """
    Measures depth of clause coverage. Multiple distinct relevant clauses
    strengthen the mapping beyond a superficial mention.
    """
    clause_ids = set()
    for c in chunks:
        meta = c.get("metadata", {})
        cid = c.get("clause_id") or meta.get("clause_id")
        if cid and str(cid).strip():
            clause_ids.add(str(cid).strip())

    count = len(clause_ids)
    if count >= 3:
        return 1.0, sorted(clause_ids)
    elif count == 2:
        return 0.75, sorted(clause_ids)
    elif count == 1:
        return 0.50, sorted(clause_ids)
    return 0.0, []


def _compute_semantic_score(
    chunks: List[Dict[str, Any]],
) -> float:
    """
    Extracts normalized retrieval fusion / reranker score from top chunks.
    """
    scores = []
    for c in chunks:
        meta = c.get("metadata", {})
        r_score = c.get("reranker_score") or meta.get("reranker_score")
        f_score = c.get("fusion_score") or meta.get("fusion_score")
        sim = c.get("similarity") or meta.get("similarity")

        if r_score is not None:
            # Map reranker logit/score roughly to [0, 1] using sigmoid
            norm_r = 1.0 / (1.0 + math.exp(-float(r_score)))
            scores.append(norm_r)
        elif f_score is not None:
            scores.append(min(1.0, float(f_score) * 20.0))
        elif sim is not None:
            scores.append(min(1.0, max(0.0, float(sim))))

    if not scores:
        return 0.5  # default neutral semantic score if retrieval didn't supply numbers

    return round(max(scores), 2)


# ============================================================
# 3. STANDARD-LEVEL AGGREGATION (Section 9, 18)
# ============================================================

def aggregate_chunks_by_standard(
    retrieved_chunks: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    Aggregates chunks by standard identity to prevent duplicate chunk proliferation.
    Key: normalized standard_number.
    Applies content-hash deduplication.
    """
    aggregated: Dict[str, Dict[str, Any]] = {}
    seen_content_hashes: Set[str] = set()

    for item in retrieved_chunks:
        # Resolve metadata
        meta = item.get("metadata", {}) if isinstance(item, dict) else {}
        std_num = (
            item.get("standard_number")
            or meta.get("standard_number")
            or ""
        )
        if not std_num or not std_num.strip():
            # Check content for explicit standard reference (e.g. "IS 3055")
            txt = (item.get("content") or item.get("source_content") or "")
            m_std = re.search(r'\bIS\s+(\d+(?:-\d+)?)\b', txt, re.IGNORECASE)
            if m_std:
                std_num = f"IS {m_std.group(1).upper()}"
            else:
                continue

        std_num_canonical = std_num.strip().upper()

        # Content hash dedup
        content_str = (item.get("content") or item.get("source_content") or "")
        chash = item.get("content_hash") or meta.get("content_hash")
        if not chash and content_str:
            chash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()[:16]

        if chash and chash in seen_content_hashes:
            continue
        if chash:
            seen_content_hashes.add(chash)

        if std_num_canonical not in aggregated:
            aggregated[std_num_canonical] = {
                "standard_number": std_num_canonical,
                "standard_id": item.get("standard_id") or meta.get("standard_id") or f"std_{std_num_canonical.replace(' ', '_')}",
                "standard_title": item.get("standard_title") or meta.get("standard_title"),
                "version_id": item.get("version_id") or meta.get("version_id"),
                "edition_or_version": item.get("edition_or_version") or meta.get("edition_or_version"),
                "document_ids": set(),
                "chunks": [],
            }

        # Update metadata if better title/version found
        if not aggregated[std_num_canonical]["standard_title"] and (item.get("standard_title") or meta.get("standard_title")):
            aggregated[std_num_canonical]["standard_title"] = item.get("standard_title") or meta.get("standard_title")
        if not aggregated[std_num_canonical]["version_id"] and (item.get("version_id") or meta.get("version_id")):
            aggregated[std_num_canonical]["version_id"] = item.get("version_id") or meta.get("version_id")

        doc_id = item.get("document_id") or meta.get("document_id")
        if doc_id:
            aggregated[std_num_canonical]["document_ids"].add(doc_id)

        aggregated[std_num_canonical]["chunks"].append(item)

    return aggregated


# ============================================================
# 3b. QCO LOOKUP & JUSTIFICATION GENERATION (PRD R2)
# ============================================================

def lookup_qco_for_standard(
    standard_number: str,
    knowledge_repo: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    Looks up matching Quality Control Order (QCO) for a standard from SQLite.
    Queries the 'qcos' table joined with 'knowledge_relationships' on 'target_entity_id'.
    Returns dictionary with is_mandatory, qco_number, regulating_authority, qco_title or None.
    """
    if not standard_number:
        return None

    std_m = re.search(r"IS(?:/IEC)?\s*(\d+)", standard_number, re.IGNORECASE)
    if not std_m:
        return None
    base_num = std_m.group(1)
    part_m = re.search(r"Part\s*(\d+)", standard_number, re.IGNORECASE)
    part_num = part_m.group(1) if part_m else None

    conn = None
    close_after = False
    try:
        if knowledge_repo and hasattr(knowledge_repo, "_conn"):
            conn = knowledge_repo._conn
        else:
            from app.config import DATA_DIR
            import sqlite3
            db_path = DATA_DIR / "knowledge" / "bis_knowledge.db"
            if db_path.exists():
                conn = sqlite3.connect(str(db_path), check_same_thread=False)
                conn.row_factory = sqlite3.Row
                close_after = True

        if conn is None:
            return None

        cursor = conn.cursor()
        query = """
            SELECT q.qco_id, q.qco_number, q.title, q.issuing_ministry, q.is_mandatory, kr.target_entity_id
            FROM qcos q
            JOIN knowledge_relationships kr ON kr.source_entity_id = q.qco_id
            WHERE kr.source_entity_type = 'QCO' AND kr.relationship_type = 'APPLIES_TO'
        """
        rows = cursor.execute(query).fetchall()

        matched_row = None
        for r in rows:
            target_str = r[5] if isinstance(r, (list, tuple)) else r["target_entity_id"]
            qb_m = re.search(r"IS(?:/IEC)?\s*(\d+)", target_str, re.IGNORECASE)
            if not qb_m:
                continue
            q_base = qb_m.group(1)
            qp_m = re.search(r"Part\s*(\d+)", target_str, re.IGNORECASE)
            q_part = qp_m.group(1) if qp_m else None

            if q_base == base_num:
                if part_num is None or q_part is None or part_num == q_part:
                    matched_row = r
                    break

        if matched_row:
            q_num = matched_row[1] if isinstance(matched_row, (list, tuple)) else matched_row["qco_number"]
            title = matched_row[2] if isinstance(matched_row, (list, tuple)) else matched_row["title"]
            ministry = matched_row[3] if isinstance(matched_row, (list, tuple)) else matched_row["issuing_ministry"]
            is_mand = bool(matched_row[4] if isinstance(matched_row, (list, tuple)) else matched_row["is_mandatory"])
            return {
                "is_mandatory": is_mand,
                "qco_number": q_num,
                "qco_title": title,
                "regulating_authority": ministry,
            }
        return None
    except Exception as e:
        logger.warning("Error querying QCO for standard %s: %s", standard_number, e)
        return None
    finally:
        if close_after and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def generate_candidate_justification(
    candidate_standard_number: str,
    standard_title: Optional[str],
    product_context: ProductContext,
    chunks: List[Dict[str, Any]],
    qco_info: Optional[Dict[str, Any]],
    mapping_status: MappingStatus,
    supporting_clause_ids: List[str],
) -> str:
    """
    Generates a template-based, evidence-grounded 'why' justification string
    referencing extracted product attributes, retrieved clauses/titles, and QCO status.
    """
    if mapping_status in (MappingStatus.WEAK_CANDIDATE, MappingStatus.INSUFFICIENT_EVIDENCE):
        return (
            f"{candidate_standard_number} match is tentative and requires verification "
            f"against the standard's scope clause (insufficient clause evidence in corpus)."
        )

    attrs = []
    if product_context.product_name:
        attrs.append(product_context.product_name)
    elif product_context.primary_identifier:
        attrs.append(product_context.primary_identifier)

    if product_context.material:
        attrs.append(f"made of {product_context.material}")
    if product_context.technology:
        attrs.append(f"utilizing {product_context.technology}")
    if product_context.intended_use:
        attrs.append(f"intended for {product_context.intended_use}")

    for k, v in (product_context.technical_characteristics or {}).items():
        val_str = str(v)
        if val_str and val_str.lower() not in " ".join(attrs).lower():
            attrs.append(f"{k.replace('_', ' ')} '{val_str}'")

    product_spec = ", ".join(attrs) if attrs else (product_context.primary_identifier or "the specified product")

    clause_ref = None
    if supporting_clause_ids:
        clause_ref = f"Clause {supporting_clause_ids[0]}"

    scope_title = None
    for ch in chunks:
        ch_meta = ch.get("metadata", {}) if isinstance(ch, dict) else {}
        heading = ch_meta.get("heading_path") or ch_meta.get("section_title") or ""
        if "scope" in heading.lower() or "requirement" in heading.lower():
            scope_title = heading.strip()
            break

    if clause_ref and scope_title:
        evidence_phrase = f", matching the scope defined in {clause_ref} ({scope_title})"
    elif clause_ref:
        evidence_phrase = f", matching the scope defined in {clause_ref}"
    elif standard_title:
        evidence_phrase = f", matching the scope defined in '{standard_title}'"
    else:
        evidence_phrase = ""

    title_suffix = f" ({standard_title})" if standard_title else ""
    if qco_info and qco_info.get("is_mandatory"):
        auth = qco_info.get("regulating_authority") or "Regulating Authority"
        order_name = qco_info.get("qco_title") or "Quality Control Order"
        order_num = qco_info.get("qco_number")
        order_ref = f"{order_name} ({order_num})" if order_num else order_name
        qco_text = f" Mandatory under {auth} {order_ref}."
    else:
        qco_text = " Mandatory QCO not identified in active records; independent gazette verification advised."

    return f"{candidate_standard_number}{title_suffix} is recommended as a candidate standard for {product_spec}{evidence_phrase}.{qco_text}"


# ============================================================
# 4. CANDIDATE SCORING & STATUS EVALUATOR (Section 6, 11, 15, 16, 17)
# ============================================================

def score_standard_candidate(
    product_context: ProductContext,
    standard_info: Dict[str, Any],
    knowledge_repo: Any = None,
    temporal_resolver: Any = None,
    evidence_confidence_level: Optional[ConfidenceLevel] = None,
) -> ProductStandardCandidate:
    """
    Computes evidence-backed mapping score, generates verifiable reasons,
    and assigns conservative MappingStatus.
    """
    std_number = standard_info["standard_number"]
    std_id = standard_info["standard_id"]
    std_title = standard_info.get("standard_title")
    version_id = standard_info.get("version_id")
    edition = standard_info.get("edition_or_version")
    chunks = standard_info.get("chunks", [])
    doc_ids = sorted(list(standard_info.get("document_ids", set())))

    reasons: List[MappingReason] = []
    chunk_ids = []
    for c in chunks:
        cid = c.get("chunk_id") or c.get("metadata", {}).get("chunk_id")
        if cid:
            chunk_ids.append(str(cid))

    # Enrich title from knowledge repository if missing
    if (not std_title or not str(std_title).strip()) and knowledge_repo:
        try:
            k_std = knowledge_repo.get_standard_by_number(std_number) or knowledge_repo.get_standard(std_id)
            if k_std and k_std.standard_title:
                std_title = k_std.standard_title
        except Exception:
            pass

    # 1. Title match signal
    s_title, title_term = _compute_title_match(product_context, std_title)
    if s_title > 0:
        reasons.append(
            MappingReason(
                reason_type=MappingReasonType.DIRECT_TITLE_MATCH,
                description=f"Standard title '{std_title}' directly matches product terms.",
                matched_term=title_term,
                score_contribution=round(s_title * W_TITLE, 4),
            )
        )

    # 2. Product term match in chunks
    s_term, term_match, term_chunk_id = _compute_product_term_match(product_context, chunks)
    if s_term > 0:
        reasons.append(
            MappingReason(
                reason_type=MappingReasonType.PRODUCT_TERM_MATCH,
                description=f"Product term(s) '{term_match}' corroborated in authoritative standard text.",
                evidence_id=term_chunk_id,
                matched_term=term_match,
                score_contribution=round(s_term * W_TERM, 4),
            )
        )

    # 3. Clause support
    s_clause, clause_ids = _compute_clause_support(chunks)
    if s_clause > 0:
        reasons.append(
            MappingReason(
                reason_type=MappingReasonType.CLAUSE_SUPPORT,
                description=f"Corroborated by {len(clause_ids)} explicit clause(s): {', '.join(clause_ids)}.",
                score_contribution=round(s_clause * W_CLAUSE, 4),
            )
        )

    # 4. Technical characteristics match
    s_tech, tech_attrs = _compute_technical_match(product_context, chunks)
    if s_tech > 0:
        reasons.append(
            MappingReason(
                reason_type=MappingReasonType.TECHNICAL_CHARACTERISTIC_MATCH,
                description=f"Explicit product characteristics verified: {', '.join(tech_attrs)}.",
                score_contribution=round(s_tech * W_TECH, 4),
            )
        )

    # 5. Semantic relevance
    s_semantic = _compute_semantic_score(chunks)
    if s_semantic > 0.3:
        reasons.append(
            MappingReason(
                reason_type=MappingReasonType.SEMANTIC_CONTENT_MATCH,
                description=f"Authoritative document text exhibits strong domain relevance.",
                score_contribution=round(s_semantic * W_SEMANTIC, 4),
            )
        )

    # 6. Knowledge graph join
    s_kg = 0.0
    if knowledge_repo:
        try:
            k_std = knowledge_repo.get_standard_by_number(std_number) or knowledge_repo.get_standard(std_id)
            if k_std:
                s_kg = 1.0
                reasons.append(
                    MappingReason(
                        reason_type=MappingReasonType.DOMAIN_MATCH,
                        description=f"Registered in authoritative BIS knowledge graph ({k_std.authority or 'BIS'}).",
                        evidence_id=k_std.standard_id,
                        score_contribution=round(1.0 * W_KG, 4),
                    )
                )
        except Exception:
            pass

    # Total mapping score calculation
    raw_score = (
        (s_title * W_TITLE)
        + (s_term * W_TERM)
        + (s_clause * W_CLAUSE)
        + (s_semantic * W_SEMANTIC)
        + (s_tech * W_TECH)
        + (s_kg * W_KG)
    )
    mapping_score = round(min(1.0, max(0.0, raw_score)), 4)

    # Check temporal status
    temporal_status = "unknown"
    temporal_uncertain = False
    if temporal_resolver and knowledge_repo:
        try:
            k_std = knowledge_repo.get_standard_by_number(std_number)
            if k_std:
                versions = knowledge_repo.list_versions_for_standard(k_std.standard_id)
                rels = knowledge_repo.list_relationships_for_standard(k_std.standard_id)
                t_res = temporal_resolver.resolve(
                    standard=k_std,
                    versions=versions,
                    amendments=[],
                    relationships=rels,
                    evidence_items=[],
                )
                temporal_status = t_res.status.value
                temporal_uncertain = t_res.requires_verification
        except Exception:
            pass

    # Assign MappingStatus with Strict Safety Gates (Section 6, 11, 16)
    # GATE 1: Title-only match is NOT enough for STRONG_CANDIDATE (Section 11)
    has_clause_or_chunk_support = (s_clause > 0 or s_term > 0 or len(chunks) > 1)

    # GATE 2: Evidence confidence gate (Section 16)
    evidence_low = (evidence_confidence_level == ConfidenceLevel.LOW)

    verification_required = False
    verification_reason = None

    if temporal_uncertain:
        verification_required = True
        verification_reason = f"Standard temporal status is uncertain or superseded ({temporal_status})."

    if (
        mapping_score >= 0.70
        and (s_title > 0 or s_term >= 0.8)
        and has_clause_or_chunk_support
        and not evidence_low
    ):
        status = MappingStatus.STRONG_CANDIDATE
    elif mapping_score >= 0.45 or (s_title >= 0.8 and not has_clause_or_chunk_support):
        status = MappingStatus.POSSIBLE_CANDIDATE
        if s_title >= 0.8 and not has_clause_or_chunk_support:
            reasons.append(
                MappingReason(
                    reason_type=MappingReasonType.DIRECT_TITLE_MATCH,
                    description="Standard title matches product name, but supporting clause content was not retrieved in corpus.",
                    matched_term=title_term,
                    score_contribution=0.0,
                )
            )

            verification_required = True
            verification_reason = "Title matched product, but supporting clause evidence is pending verification."
    elif mapping_score >= 0.20:
        status = MappingStatus.WEAK_CANDIDATE
    else:
        status = MappingStatus.INSUFFICIENT_EVIDENCE
        verification_required = True
        verification_reason = "Available evidence does not adequately corroborate standard coverage."

    # 7. Quality Control Order (QCO) Lookup (PRD R2)
    qco_info = lookup_qco_for_standard(std_number, knowledge_repo=knowledge_repo)
    if qco_info:
        is_mandatory = True
        qco_number = qco_info["qco_number"]
        regulating_authority = qco_info["regulating_authority"]
    else:
        is_mandatory = False
        qco_number = None
        regulating_authority = None

    justification = generate_candidate_justification(
        candidate_standard_number=std_number,
        standard_title=std_title,
        product_context=product_context,
        chunks=chunks,
        qco_info=qco_info,
        mapping_status=status,
        supporting_clause_ids=clause_ids,
    )

    mapping_id = f"map_{product_context.product_context_id[:8]}_{std_number.replace(' ', '_')}"

    return ProductStandardCandidate(
        mapping_id=mapping_id,
        product_context_id=product_context.product_context_id,
        standard_id=std_id,
        standard_number=std_number,
        standard_title=std_title,
        version_id=version_id,
        edition_or_version=edition,
        mapping_status=status,
        mapping_score=mapping_score,
        mapping_reasons=reasons,
        supporting_evidence_ids=chunk_ids,
        supporting_clause_ids=clause_ids,
        source_document_ids=doc_ids,
        temporal_status=temporal_status,
        confidence_score=mapping_score,  # evidence-backed candidate fit
        verification_required=verification_required,
        verification_reason=verification_reason,
        is_mandatory=is_mandatory,
        qco_number=qco_number,
        regulating_authority=regulating_authority,
        justification=justification,
        created_at=time.time(),
    )


# ============================================================
# 5. CANDIDATE DISCOVERY SERVICE (Section 8, 19, 20, 25)
# ============================================================

def discover_standard_candidates(
    product_context: ProductContext,
    retriever: Any = None,
    reranker: Any = None,
    knowledge_repo: Any = None,
    temporal_resolver: Any = None,
    top_k: int = 5,
    evidence_confidence_level: Optional[ConfidenceLevel] = None,
) -> List[ProductStandardCandidate]:
    """
    Discovers candidate BIS standards for a given ProductContext.
    Uses existing retriever and knowledge graph. Aggregates at standard level.
    Returns ranked list of ProductStandardCandidate objects.
    """
    if product_context.is_empty:
        logger.info("Empty ProductContext provided; returning empty candidates.")
        return []

    # 1. Generate controlled retrieval queries
    queries = generate_candidate_queries(product_context)
    all_retrieved: List[Dict[str, Any]] = []

    # 2. Retrieve candidates across queries
    if retriever:
        for q in queries:
            try:
                # Use strategy=STANDARD_DISCOVERY if supported
                res = retriever.retrieve(query=q, top_k=top_k)
                if res:
                    all_retrieved.extend(res)
            except Exception as e:
                logger.warning("Retrieval failed for query '%s': %s", q, e)

    # 3. If knowledge_repo exists, check for direct standard number or title matches
    known_standards = []
    if knowledge_repo:
        try:
            # Check explicit standards from product context
            for std_num in product_context.existing_standards:
                k_std = knowledge_repo.get_standard_by_number(std_num)
                if k_std and k_std not in known_standards:
                    known_standards.append(k_std)

            # Check standards known in the repo
            all_stds = knowledge_repo.list_standards()
            for std in all_stds:
                title_score, _ = _compute_title_match(product_context, std.standard_title)
                if title_score > 0.4 and std not in known_standards:
                    known_standards.append(std)
        except Exception as e:
            logger.warning("Knowledge repo scan error: %s", e)


    # 4. Standard-level aggregation
    aggregated = aggregate_chunks_by_standard(all_retrieved)

    # Merge knowledge repo standards if not already present from chunks
    for k_std in known_standards:
        std_num_canon = k_std.standard_number.upper()
        if std_num_canon not in aggregated:
            aggregated[std_num_canon] = {
                "standard_number": k_std.standard_number,
                "standard_id": k_std.standard_id,
                "standard_title": k_std.standard_title,
                "version_id": k_std.document_id,
                "edition_or_version": k_std.edition_or_version,
                "document_ids": {k_std.document_id} if k_std.document_id else set(),
                "chunks": [],
            }

    # 5. Score candidates
    candidates: List[ProductStandardCandidate] = []
    for std_num, std_info in aggregated.items():
        candidate = score_standard_candidate(
            product_context=product_context,
            standard_info=std_info,
            knowledge_repo=knowledge_repo,
            temporal_resolver=temporal_resolver,
            evidence_confidence_level=evidence_confidence_level,
        )
        candidates.append(candidate)

    # 6. Rank candidates deterministically by mapping_score descending, standard_number ascending
    candidates = rank_standard_candidates(candidates)

    return candidates[:top_k]


def rank_standard_candidates(
    candidates: List[ProductStandardCandidate],
) -> List[ProductStandardCandidate]:
    """
    Ranks standard candidates deterministically.
    Status priority: STRONG_CANDIDATE > POSSIBLE_CANDIDATE > WEAK_CANDIDATE > INSUFFICIENT_EVIDENCE
    Tie breaker: mapping_score descending, standard_number ascending.
    """
    status_order = {
        MappingStatus.STRONG_CANDIDATE: 4,
        MappingStatus.POSSIBLE_CANDIDATE: 3,
        MappingStatus.WEAK_CANDIDATE: 2,
        MappingStatus.INSUFFICIENT_EVIDENCE: 1,
        MappingStatus.VERIFICATION_REQUIRED: 0,
    }

    def _key(c: ProductStandardCandidate):
        st_weight = status_order.get(c.mapping_status, 0)
        return (-st_weight, -c.mapping_score, c.standard_number)

    return sorted(candidates, key=_key)


def explain_standard_mapping(
    candidate: ProductStandardCandidate,
) -> Dict[str, Any]:
    """
    Returns an explainability breakdown of why a standard was mapped.
    Explanations trace directly to observable evidence.
    """
    return {
        "standard_number": candidate.standard_number,
        "standard_title": candidate.standard_title,
        "mapping_status": candidate.mapping_status.value,
        "mapping_score": candidate.mapping_score,
        "is_mandatory": candidate.is_mandatory,
        "qco_number": candidate.qco_number,
        "regulating_authority": candidate.regulating_authority,
        "justification": candidate.justification,
        "reasons": [r.to_dict() for r in candidate.mapping_reasons],
        "supporting_clause_ids": candidate.supporting_clause_ids,
        "supporting_evidence_ids": candidate.supporting_evidence_ids,
        "verification_required": candidate.verification_required,
        "verification_reason": candidate.verification_reason,
    }

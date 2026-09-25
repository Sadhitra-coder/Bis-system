"""
app/grounding/validator.py

Deterministic post-generation grounding validator.
Evaluates generated answer claims against authoritative source content and
retrieval evidence.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.evidence.models import EvidenceItem
from app.grounding.models import (
    AnswerClaim,
    Citation,
    ClaimType,
    GroundingResult,
    GroundingStatus,
    SupportStatus,
)

logger = logging.getLogger(__name__)

# Stopwords for lexical overlap check
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "when", "at",
    "by", "for", "with", "about", "against", "between", "into", "through",
    "during", "before", "after", "above", "below", "to", "from", "up", "down",
    "in", "out", "on", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "all", "any", "both", "each", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same",
    "so", "than", "too", "very", "can", "will", "just", "should", "now",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "having", "do", "does", "did", "doing", "would", "could", "shall", "may",
    "must", "might", "this", "that", "these", "those", "it", "its", "as",
    "which", "who", "whom", "whose", "where", "why", "how", "since", "while",
    "also", "well", "both", "either", "neither", "though", "although", "even",
}

# Domain framing and structural words for technical regulatory prose
_DOMAIN_FRAMING_WORDS = {
    "requirement", "requirements", "standard", "standards", "include", "includes", "included", "including",
    "specification", "specifications", "provision", "provisions", "quality", "assurance",
    "overview", "various", "general", "compliance", "compliant", "applies", "applicable", "following",
    "details", "outlined", "accordance", "relevant", "stated", "provides", "provided", "providing",
    "guidelines", "procedure", "procedures", "ensuring", "ensure", "ensures", "conducted",
    "conduct", "per", "covered", "covers", "covering", "related", "relates", "relating",
    "clause", "clauses", "section", "sections", "table", "tables", "part", "parts",
    "proper", "properly", "mechanism", "mechanisms", "feature", "features", "manner",
    "used", "using", "uses", "use", "meet", "meets", "meeting", "met", "safe", "safety",
    "system", "systems", "types", "type", "method", "methods", "rules", "rule", "item", "items",
    "exist", "exists", "aspect", "aspects", "clear", "confirmed", "confirm", "confirms",
    # Temporal, version, calendar, and structural formatting words
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "source", "sources", "reference", "references", "status", "currently", "force", "active", "edition", "revision", "supersedes", "amendment", "amendments", "withdrawn", "effective", "published",
}

# Number words to digit mapping
_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20",
}


def _stem(w: str) -> str:
    w = w.lower()
    for suffix in ("ation", "ations", "ment", "ments", "ing", "tion", "tions", "ies", "ied", "ed", "ly", "es", "s"):
        if len(w) > len(suffix) + 2 and w.endswith(suffix):
            return w[:-len(suffix)]
    return w

_DOMAIN_FRAMING_STEMS = {_stem(w) for w in _DOMAIN_FRAMING_WORDS}

_STANDARD_TITLES_CACHE: Dict[str, str] = {}

def _get_standard_title(standard_number: str) -> str:
    if not standard_number:
        return ""
    std_clean = standard_number.strip().upper()
    if std_clean in _STANDARD_TITLES_CACHE:
        return _STANDARD_TITLES_CACHE[std_clean]
    try:
        import sqlite3
        import os
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "knowledge", "bis_knowledge.db")
        if not os.path.exists(db_path):
            db_path = "data/knowledge/bis_knowledge.db"
        conn = sqlite3.connect(db_path, timeout=5.0)
        c = conn.cursor()
        c.execute("SELECT title FROM standards WHERE standard_number = ? LIMIT 1", (std_clean,))
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            _STANDARD_TITLES_CACHE[std_clean] = row[0]
            return row[0]
    except Exception:
        pass
    _STANDARD_TITLES_CACHE[std_clean] = ""
    return ""

# Negative assertion phrases that convert absence of evidence into negative domain facts
_NEGATIVE_ASSERTION_PATTERNS = [
    re.compile(r"\b(?:does\s+not\s+exist|do\s+not\s+exist|no\s+such\s+standard\s+exists)\b", re.IGNORECASE),
    re.compile(r"\b(?:no\s+requirement\s+exists|is\s+not\s+required\s+by\s+the\s+standard|the\s+standard\s+does\s+not\s+require)\b", re.IGNORECASE),
    re.compile(r"\b(?:there\s+are\s+no\s+(?:testing|sampling|marking|calibration|certification)\s+requirements)\b", re.IGNORECASE),
]

# Legal/regulatory conclusion phrases that extrapolate beyond technical standards
_LEGAL_CONCLUSION_PATTERNS = [
    re.compile(r"\b(?:must\s+obtain\s+certification\s+before\s+sale|prohibited\s+from\s+selling|illegal\s+to\s+sell)\b", re.IGNORECASE),
    re.compile(r"\b(?:officially\s+compliant|ai\s+certified|approved\s+by\s+bis|legally\s+binding\s+certification)\b", re.IGNORECASE),
    re.compile(r"\b(?:superseded\s+by\s+law|legally\s+invalid)\b", re.IGNORECASE),
]

# Phase 9 Temporal & Currentness claim patterns (Sections 19, 21, 23)
_CURRENTNESS_CLAIM_PATTERN = re.compile(
    r"\b(?:is\s+the\s+current\s+(?:edition|standard|version|specification)|"
    r"the\s+current\s+requirement\s+is|"
    r"is\s+currently\s+(?:mandatory|in\s+force|effective|required|binding)|"
    r"current\s+edition\s+of\s+IS|"
    r"latest\s+and\s+current\s+version|"
    r"active\s+and\s+currently\s+enforced)\b",
    re.IGNORECASE,
)

_SUPERSESSION_CLAIM_PATTERN = re.compile(
    r"\b(?:supersedes\s+(?:the\s+)?(?:earlier\s+edition|IS\s+\d+|[0-9]{4}\s+edition)|"
    r"this\s+edition\s+supersedes|"
    r"in\s+supersession\s+of\s+IS)\b",
    re.IGNORECASE,
)

_AMENDMENT_REPLACE_PATTERN = re.compile(
    r"\b(?:Amendment\s*(\d+)\s*(?:replaces|substitutes|modifies)\s*Clause\s*(\d+(?:\.\d+)*))\b",
    re.IGNORECASE,
)


class GroundingValidator:
    """
    Deterministic validator for generated claims against retrieved evidence.
    """

    @staticmethod
    def extract_claims_from_text(text: str) -> List[AnswerClaim]:
        """
        Segments plain text into claims and extracts inline citation tokens like [EV1].
        Used when LLM outputs plain text or structured parsing fails.
        """
        if not text or not text.strip():
            return []

        # Check for separated Sources line at the end (e.g. "Sources: [EV1], [EV2]")
        sources_match = re.search(r"(?:Sources|References)\s*:\s*((?:\[(?:EV\d+|citation-\d+)\][,\s]*)+)", text, re.IGNORECASE)
        fallback_citation_ids = []
        if sources_match:
            fallback_citation_ids = [c.upper() for c in re.findall(r"\[(EV\d+|citation-\d+)\]", sources_match.group(1), re.IGNORECASE)]

        # Strip trailing Sources / References block before splitting into claims
        text_without_sources = re.sub(r"\n*(?:\*\*Sources:\*\*|Sources:|References:).*$", "", text, flags=re.DOTALL | re.IGNORECASE).strip()

        claims: List[AnswerClaim] = []
        # Split by sentence boundaries, bullet items, or paragraph breaks
        raw_sentences = re.split(r"(?<=[.!?])\s+|\n{2,}|\n(?=[-*•]|\d+\.)", text_without_sources)
        claim_counter = 1

        for raw_s in raw_sentences:
            s = raw_s.strip()
            if not s:
                continue

            # Strip leading bullet dashes or numbers
            s = re.sub(r"^[-*•\d.]+\s*", "", s).strip()
            if not s:
                continue

            # Extract citations like [EV1], [EV2], or [citation-1]
            citation_matches = re.findall(r"\[(EV\d+|citation-\d+)\]", s, re.IGNORECASE)
            # Normalize citation tokens (e.g. EV1); track whether citations are inline or from footer
            uses_footer = False
            if citation_matches:
                citation_ids = [c.upper() for c in citation_matches]
            else:
                citation_ids = list(fallback_citation_ids)
                uses_footer = bool(fallback_citation_ids)

            # Clean text of citation brackets for analysis
            clean_text = re.sub(r"\[(EV\d+|citation-\d+)\]", "", s).strip()
            if not clean_text:
                continue

            # Skip the separated Sources header line itself
            clean_lower = clean_text.lower().strip("*: \t\n,")
            if clean_lower in ("sources", "source", "references", "reference") or clean_lower.startswith("sources:") or clean_lower.startswith("references:"):
                continue

            # Skip consumer verification tip sentence (mandated by PRD R5, not technical claims)
            if "bis care" in clean_lower or "authenticity of the isi mark" in clean_lower:
                continue

            # Skip editorial status headlines (e.g. "**Status: Currently in force**")
            if clean_lower.startswith("status:") or clean_lower in ("currently in force", "verification required"):
                continue

            # Determine claim type
            lower = clean_text.lower()
            if any(p in lower for p in [
                "not found in the retrieved", "could not find", "not specified in the retrieved",
                "does not specify", "cannot be determined from the available", "unclear from the retrieved",
                "no information was retrieved", "verification required",
            ]):
                claim_type = ClaimType.UNCERTAINTY
            elif any(p in lower for p in ["suggests", "implies", "may indicate", "appears to", "it seems"]):
                claim_type = ClaimType.INTERPRETATION
            else:
                claim_type = ClaimType.FACT

            claims.append(
                AnswerClaim(
                    claim_id=f"C{claim_counter}",
                    text=clean_text,
                    claim_type=claim_type,
                    citation_ids=citation_ids,
                    uses_footer_citations=uses_footer,
                )
            )
            claim_counter += 1

        return claims

    @staticmethod
    def extract_numbers_and_quantities(text: str) -> List[str]:
        """
        Extracts numbers, measurements, percentages, and quantities from text,
        ignoring standard numbers (e.g. IS 3055) and clause numbers (e.g. Clause 4.1).
        """
        # Mask out standard numbers (IS XXXX:YYYY) and clauses (Clause X.X)
        masked = re.sub(r"\bIS\s*\d+(?:\s*[:/ -]\s*\d{4})?\b", " ", text, flags=re.IGNORECASE)
        masked = re.sub(r"\bClause\s*\d+(?:\.\d+)*\b", " ", masked, flags=re.IGNORECASE)
        masked = re.sub(r"\b(?:Part|Amd|Amendment|Edition)\s*\d+\b", " ", masked, flags=re.IGNORECASE)
        masked = re.sub(r"\b\[(?:EV\d+|citation-\d+)\]", " ", masked, flags=re.IGNORECASE)

        results: List[str] = []

        # Measurements with units: 6 months, 500 V, 10 mm, 5 %, 10 pieces, 1,100 V
        unit_pattern = re.compile(
            r"\b(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(months?|years?|days?|hours?|weeks?|mm|cm|m|kg|g|v|kv|hz|%|percent|samples?|pieces?|units?|tests?|batches?|deg|°c)\b",
            re.IGNORECASE,
        )
        for m in unit_pattern.finditer(masked):
            results.append(m.group(0).lower().strip())

        # Isolated quantities / numbers
        num_pattern = re.compile(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b")
        for m in num_pattern.finditer(masked):
            val = m.group(0)
            if val not in [r.split()[0] for r in results]:
                results.append(val)

        # Word numbers (e.g. "six months", "ten pieces")
        word_num_pattern = re.compile(
            r"\b(one|two|three|four|five|six|seven|eight|nine|ten|twelve|twenty)\s+(months?|years?|days?|hours?|weeks?|samples?|pieces?|units?|tests?)\b",
            re.IGNORECASE,
        )
        for m in word_num_pattern.finditer(masked):
            word = m.group(1).lower()
            digit = _NUMBER_WORDS.get(word, word)
            results.append(f"{digit} {m.group(2).lower()}")

        return list(dict.fromkeys(results))

    @classmethod
    def validate_claim(
        cls,
        claim: AnswerClaim,
        citation_map: Dict[str, EvidenceItem],
    ) -> AnswerClaim:
        """
        Validates an individual claim against its cited evidence items.
        Modifies claim in-place and returns it.
        """
        issues: List[str] = []
        valid_citations: List[EvidenceItem] = []

        # 1. Missing citations check for factual claims
        if not claim.citation_ids:
            if claim.claim_type == ClaimType.FACT:
                # Meta-sentences or purely transitional statements can be soft, but factual claims need citations
                issues.append("missing_citation")
                claim.support_status = SupportStatus.UNVERIFIABLE
                claim.issues = issues
                claim.validation_notes = "Factual claim lacks required evidence citations."
                return claim
            elif claim.claim_type == ClaimType.UNCERTAINTY:
                # Statements of uncertainty/absence don't require positive citations
                claim.support_status = SupportStatus.SUPPORTED
                claim.validation_notes = "Uncertainty / absence claim correctly acknowledges documentation limits."
                return claim
            else:
                claim.support_status = SupportStatus.PARTIALLY_SUPPORTED
                claim.validation_notes = "Interpretation claim lacks explicit citation."
                return claim

        # 2. Check citation existence (Section 3 & 4)
        for cid in claim.citation_ids:
            if cid not in citation_map:
                issues.append(f"fake_or_unknown_citation_id:{cid}")
            else:
                valid_citations.append(citation_map[cid])

        if not valid_citations:
            claim.support_status = SupportStatus.UNSUPPORTED
            claim.issues = issues
            claim.validation_notes = "All cited evidence IDs are invalid or non-existent."
            return claim

        # Deduplicate cited evidence by content_hash (Section 25)
        unique_hashes = set()
        unique_citations: List[EvidenceItem] = []
        for ev in valid_citations:
            h = getattr(ev, "content_hash", None) or getattr(ev, "chunk_id", None)
            if h not in unique_hashes:
                unique_hashes.add(h)
                unique_citations.append(ev)

        claim.supporting_citation_count = len(unique_citations)

        # 3. Negation / Epistemic honesty check (Section 11)
        for neg_pat in _NEGATIVE_ASSERTION_PATTERNS:
            if neg_pat.search(claim.text):
                # Check if ANY cited source content explicitly makes this negative claim
                found_in_evidence = any(neg_pat.search(ev.source_content or ev.content) for ev in unique_citations)
                if not found_in_evidence:
                    issues.append("unsupported_negative_assertion:absence_converted_to_negative_fact")

        # 4. Legal / Regulatory extrapolation check (Section 24)
        for legal_pat in _LEGAL_CONCLUSION_PATTERNS:
            if legal_pat.search(claim.text):
                found_in_evidence = any(legal_pat.search(ev.source_content or ev.content) for ev in unique_citations)
                if not found_in_evidence:
                    issues.append("unsupported_legal_conclusion")

        # 5. Identifier validation: Standard number, Clause, Version (Section 13)
        # 5. Identifier validation: Standard number, Clause, Version (Section 13)
        # Check standard number in claim: e.g. "IS 3055" (require capital IS and 2-6 digits to prevent matching English 'is 1')
        std_matches = re.findall(r"\bIS\s*([1-9]\d{1,5})\b", claim.text)
        if std_matches:
            for std_num in std_matches:
                matching_ev = any(
                    (ev.standard_number and std_num in ev.standard_number)
                    or (f"IS {std_num}" in (ev.source_content or ev.content))
                    for ev in unique_citations
                )
                if not matching_ev:
                    issues.append(f"standard_mismatch:claim=IS {std_num}")

        # Check clause in claim: e.g. "Clause 4.1" or "Clause 6"
        clause_matches = re.findall(r"\bClause\s*(\d+(?:\.\d+)*)\b", claim.text, re.IGNORECASE)
        if clause_matches:
            for cl in clause_matches:
                cl_has_dot = "." in cl
                matching_ev = any(
                    (ev.clause_id and (ev.clause_id == cl or ev.clause_id.startswith(f"{cl}.") or cl in ev.clause_id))
                    or bool(re.search(rf"\b(?:Clause|Cl\.?|Section|Sec\.?|see|per)?\s*{re.escape(cl)}\b", ev.source_content or ev.content, re.IGNORECASE))
                    or (cl_has_dot and bool(re.search(rf"\b{re.escape(cl)}\b", ev.source_content or ev.content)))
                    or bool(re.search(rf"\b{re.escape(cl)}\s+(?:of\s+IS|shall|is|are|RATING|CLASSIFICATION|[A-Z]{{3,}})\b", ev.source_content or ev.content, re.IGNORECASE))
                    for ev in unique_citations
                )
                if not matching_ev:
                    issues.append(f"clause_mismatch:claim=Clause {cl}")

        # Check version / edition / amendment in claim
        if "third edition" in claim.text.lower():
            matching_ver = any(
                ev.edition_or_version and "third" in ev.edition_or_version.lower()
                for ev in unique_citations
            )
            if not matching_ver:
                issues.append("version_mismatch:claim=Third Edition")

        amd_matches = re.findall(r"\b(?:Amendment|Amd)(?:\s+No\.?)?\s*([1-9]\d?)\b", claim.text, re.IGNORECASE)
        if amd_matches:
            for amd in amd_matches:
                matching_amd = any(
                    ev.amendment_number and str(ev.amendment_number) == amd
                    for ev in unique_citations
                )
                if not matching_amd:
                    issues.append(f"amendment_mismatch:claim=Amendment {amd}")

        # 6. Numerical / Date / Quantity validation (Section 12)
        # Source text must support numbers.
        # Enrich source text with official standard titles, clause titles, and metadata
        content_parts = []
        for ev in unique_citations:
            content_parts.append(ev.source_content or ev.content)
            if ev.standard_title:
                content_parts.append(ev.standard_title)
            elif ev.standard_number:
                std_title = _get_standard_title(ev.standard_number)
                if std_title:
                    content_parts.append(std_title)
            if ev.clause_title:
                content_parts.append(ev.clause_title)
            if ev.section:
                content_parts.append(ev.section)
            meta = getattr(ev, "metadata", {}) or {}
            for k in ("standard_title", "title", "document_title", "clause_title"):
                if meta.get(k):
                    content_parts.append(str(meta[k]))
        combined_source_content = " ".join(content_parts).lower()

        non_ascii_chars = sum(1 for c in claim.text if ord(c) > 0x0080)
        total_chars = max(len(claim.text.strip()), 1)
        is_non_latin_claim = non_ascii_chars / total_chars > 0.30  # >30% non-ASCII = multilingual

        is_footer_cited = getattr(claim, "uses_footer_citations", False)

        if not is_non_latin_claim:
            quantities = cls.extract_numbers_and_quantities(claim.text)
            masked_source = re.sub(r"\bIS\s*\d+(?:\s*[:/ -]\s*\d{4})?\b", " ", combined_source_content, flags=re.IGNORECASE)
            masked_source = re.sub(r"\bClause\s*\d+(?:\.\d+)*\b", " ", masked_source, flags=re.IGNORECASE)

            source_clean = combined_source_content.replace(",", "")
            masked_source_clean = masked_source.replace(",", "")

            for qty in quantities:
                qty_lower = qty.lower()
                qty_clean = qty_lower.replace(",", "")
                if qty_lower not in combined_source_content and qty_clean not in source_clean:
                    parts = qty_clean.split()
                    if len(parts) == 2:
                        num, unit = parts
                        unit_stem = unit.rstrip("s")
                        num_pattern = re.compile(rf"\b{re.escape(num)}\b")
                        if is_footer_cited:
                            # For footer-cited claims: only require the bare number appears
                            # in source — don't penalise differing unit format
                            if not num_pattern.search(masked_source_clean) and not num_pattern.search(source_clean):
                                issues.append(f"unsupported_numerical_value:{qty}")
                        else:
                            if (unit_stem not in source_clean and unit not in source_clean) or not num_pattern.search(masked_source_clean):
                                issues.append(f"unsupported_numerical_value:{qty}")
                    else:
                        num_pattern = re.compile(rf"\b{re.escape(qty_clean)}\b")
                        is_metadata_year = (
                            len(qty_clean) == 4
                            and qty_clean.isdigit()
                            and any(
                                str(getattr(ev, "standard_year", None) or "") == qty_clean
                                or qty_clean in (getattr(ev, "standard_number", None) or "")
                                or qty_clean in source_clean
                                for ev in unique_citations
                            )
                        )
                        if not is_metadata_year and not num_pattern.search(masked_source_clean) and not num_pattern.search(source_clean):
                            issues.append(f"unsupported_numerical_value:{qty}")

        # 7. Semantic / Content Support
        # Check token overlap between claim and cited source_content.
        # Skip for non-Latin claims: cross-lingual comparison of Hindi sentences against
        # English source content produces meaningless results.
        # Footer-cited claims get a relaxed threshold (document-level attribution is
        # inherently less granular than per-sentence inline citation).
        overlap_threshold_hard = 0.25 if is_footer_cited else 0.40
        overlap_threshold_soft = 0.45 if is_footer_cited else 0.60

        claim_words = {} if is_non_latin_claim else {
            w for w in re.findall(r"\b[a-zA-Z]{3,}\b", claim.text.lower())
            if w not in _STOPWORDS and w not in _DOMAIN_FRAMING_WORDS and _stem(w) not in _DOMAIN_FRAMING_STEMS
        }
        if claim_words:
            source_words = set(re.findall(r"\b[a-zA-Z]{3,}\b", combined_source_content))
            source_stems = {_stem(w) for w in source_words}

            matched_words = set()
            for w in claim_words:
                sw = _stem(w)
                if (
                    w in source_words
                    or sw in source_stems
                    or sw in combined_source_content
                    or (w == "pvc" and ("polyvinyl chloride" in combined_source_content or "pvc" in combined_source_content))
                    or any(w in src or sw in src for src in source_words if len(src) >= 4)
                ):
                    matched_words.add(w)

            unsupported_words = claim_words - matched_words
            overlap_ratio = len(matched_words) / len(claim_words)
            missing_ratio = len(unsupported_words) / len(claim_words)

            if overlap_ratio < overlap_threshold_hard or missing_ratio >= 0.60:
                issues.append(f"unsupported_terms:missing={','.join(sorted(unsupported_words))}")
            elif overlap_ratio < overlap_threshold_soft:
                issues.append(f"partial_content_support:overlap={overlap_ratio:.2f}")

        # 8. Phase 9 Temporal & Currentness Grounding Checks (Sections 19, 21, 23)
        # A. Currentness assertion requires explicit temporal evidence (not publication date alone)
        # EXCEPTION: for footer-cited claims (new 4-part format), the temporal resolver already
        # validated currentness at the pipeline level — skip the per-sentence re-check to avoid
        # false positives from document-level citation attribution.
        if _CURRENTNESS_CLAIM_PATTERN.search(claim.text) and not is_footer_cited:
            has_temporal_proof = False
            for ev in unique_citations:
                meta = getattr(ev, "metadata", {}) or {}
                text = (ev.source_content or ev.content).lower()
                # Explicit supersession / in force statements in text
                if any(w in text for w in [
                    "supersedes", "comes into force", "effective from",
                    "stands current", "hereby notified",
                    # Revision/edition language that establishes the document's currentness
                    "fourth revision", "third revision", "second revision", "first revision",
                    "latest revision", "latest edition", "current edition",
                    "this standard supersedes", "in supersession",
                ]):
                    has_temporal_proof = True
                    break
                # Explicit database or metadata validation
                if meta.get("is_current") is True or meta.get("status") in ("effective", "current", "in_force"):
                    has_temporal_proof = True
                    break
                # If the standard's own publication year appears in the evidence, treat as current
                std_year = str(getattr(ev, "standard_year", None) or "")
                if std_year and std_year in text and any(w in text for w in ["revision", "edition", "published", "issued"]):
                    has_temporal_proof = True
                    break
            if not has_temporal_proof:
                issues.append("unsupported_currentness_claim:publication_date_or_text_alone_does_not_establish_current_status")

        # B. Supersession assertion requires explicit supersession language in source text
        if _SUPERSESSION_CLAIM_PATTERN.search(claim.text):
            has_supersession_proof = any(
                any(w in (ev.source_content or ev.content).lower() for w in ["supersedes", "superseding", "in supersession of", "cancels and replaces"])
                for ev in unique_citations
            )
            if not has_supersession_proof:
                issues.append("unsupported_supersession_claim:no_explicit_supersession_evidence")

        # C. Amendment modifying specific clause requires explicit clause reference in amendment
        m_amd = _AMENDMENT_REPLACE_PATTERN.search(claim.text)
        if m_amd:
            target_clause = m_amd.group(2)
            has_clause_in_amd = any(
                target_clause in (ev.source_content or ev.content)
                for ev in unique_citations
            )
            if not has_clause_in_amd:
                issues.append(f"unsupported_amendment_clause_modification:Clause_{target_clause}_not_explicitly_mentioned_in_amendment")

        # Determine Support Status
        claim.issues = issues

        # Only severe grounding violations make a claim UNSUPPORTED:
        # - Fake or unknown citation tokens
        # - Actual standard or clause mismatches
        # - Completely unsupported numerical values
        # - Unsupported negative assertions (converting absence into negative fact)
        # - Unsupported legal conclusions
        # - Severe vocabulary gap (4 or more terms completely missing from source)
        # - Unsupported currentness / supersession / amendment claims
        missing_terms_count = 0
        for iss in issues:
            if iss.startswith("unsupported_terms:missing="):
                missing_terms_count = len(iss.split("=")[1].split(","))

        has_specific_entities = bool(quantities or clause_matches or std_matches)
        severe_terms_gap = (missing_terms_count >= 4 and has_specific_entities)

        has_severe_issues = any(
            iss.startswith("fake_or_unknown_citation_id")
            or "mismatch" in iss
            or iss.startswith("unsupported_numerical_value")
            or iss.startswith("unsupported_negative_assertion")
            or iss.startswith("unsupported_legal_conclusion")
            or severe_terms_gap
            or iss.startswith("unsupported_currentness_claim")
            or iss.startswith("unsupported_supersession_claim")
            or iss.startswith("unsupported_amendment_clause_modification")
            for iss in issues
        )

        if not issues:
            claim.support_status = SupportStatus.SUPPORTED
            claim.validation_notes = f"Verified across {len(unique_citations)} independent citation(s)."
        elif has_severe_issues:
            claim.support_status = SupportStatus.UNSUPPORTED
            claim.validation_notes = f"Grounding check failed: {'; '.join(issues)}."
        else:
            # Minor issues, like low content overlap (<4 missing terms) or minor partial match
            claim.support_status = SupportStatus.PARTIALLY_SUPPORTED
            claim.validation_notes = f"Partially supported: {'; '.join(issues)}."

        return claim

    @classmethod
    def validate(
        cls,
        answer: str,
        evidence_items: List[EvidenceItem],
        raw_claims: Optional[List[Dict[str, Any]]] = None,
    ) -> GroundingResult:
        """
        Complete post-generation validation pass.
        """
        # Build canonical citation map: EV1 -> EvidenceItem
        citation_map: Dict[str, EvidenceItem] = {}
        citations_list: List[Citation] = []

        for idx, ev in enumerate(evidence_items, start=1):
            token = f"EV{idx}"
            citation_map[token] = ev
            citations_list.append(Citation.from_evidence(ev, token))

        # Parse / extract claims
        claims: List[AnswerClaim] = []
        if raw_claims:
            for idx, rc in enumerate(raw_claims, start=1):
                if isinstance(rc, AnswerClaim):
                    claims.append(rc)
                    continue
                cid = rc.get("claim_id") or f"C{idx}"
                text = rc.get("text", "").strip()
                if not text:
                    continue
                ctype_str = rc.get("claim_type", "fact").lower()
                try:
                    ctype = ClaimType(ctype_str)
                except ValueError:
                    ctype = ClaimType.FACT

                cids = rc.get("citation_ids", [])
                if isinstance(cids, str):
                    cids = [cids]
                # Also inspect claim text for inline tokens like [EV1]
                inline_cids = re.findall(r"\[(EV\d+|citation-\d+)\]", text, re.IGNORECASE)
                combined_cids = list(dict.fromkeys([c.upper() for c in cids + inline_cids]))

                claims.append(
                    AnswerClaim(
                        claim_id=cid,
                        text=re.sub(r"\[(EV\d+|citation-\d+)\]", "", text).strip(),
                        claim_type=ctype,
                        citation_ids=combined_cids,
                    )
                )

        if not claims and answer:
            claims = cls.extract_claims_from_text(answer)

        # Normalize boilerplate consumer guidance and headline claims
        for claim in claims:
            txt_lower = claim.text.lower().strip("*: \t\n,")
            if "bis care" in txt_lower or "authenticity of the isi mark" in txt_lower:
                claim.claim_type = ClaimType.INTERPRETATION
                claim.support_status = SupportStatus.SUPPORTED
                claim.issues = []
                claim.validation_notes = "Official consumer guidance notice."
            elif txt_lower.startswith("status:") or txt_lower in ("currently in force", "verification required"):
                claim.claim_type = ClaimType.INTERPRETATION
                claim.support_status = SupportStatus.SUPPORTED
                claim.issues = []
                claim.validation_notes = "Editorial status headline."

        # Validate each substantive claim
        for claim in claims:
            if claim.support_status == SupportStatus.SUPPORTED and claim.claim_type == ClaimType.INTERPRETATION:
                continue
            cls.validate_claim(claim, citation_map)

        # Compute metrics
        total_claims = len(claims)
        factual_claims = [c for c in claims if c.claim_type == ClaimType.FACT]
        supported_factual = [c for c in factual_claims if c.support_status == SupportStatus.SUPPORTED]
        supported_all = [c for c in claims if c.support_status == SupportStatus.SUPPORTED]
        partial_all = [c for c in claims if c.support_status == SupportStatus.PARTIALLY_SUPPORTED]
        unsupported_all = [c for c in claims if c.support_status == SupportStatus.UNSUPPORTED]

        claims_with_valid_cits = sum(
            1 for c in claims if any(cid in citation_map for cid in c.citation_ids)
        )
        claims_without_cits = sum(1 for c in claims if not c.citation_ids)

        if factual_claims:
            citation_coverage = len(supported_factual) / len(factual_claims)
        else:
            citation_coverage = 1.0

        if total_claims > 0:
            score = (len(supported_all) * 1.0 + len(partial_all) * 0.5) / total_claims
        else:
            score = 1.0 if not answer or "Verification Required" in answer else 0.0

        # Determine overall grounding status
        if unsupported_all:
            status = GroundingStatus.UNSUPPORTED
            unsupported_texts = [f"{c.claim_id}: {c.text} ({'; '.join(c.issues)})" for c in unsupported_all]
            reason = f"Grounding validation detected {len(unsupported_all)} unsupported claim(s)."
            summary = "; ".join(unsupported_texts)
        elif partial_all:
            status = GroundingStatus.PARTIALLY_GROUNDED
            reason = f"{len(partial_all)} claim(s) are partially supported by cited evidence."
            summary = "; ".join(f"{c.claim_id}: {c.text}" for c in partial_all)
        elif total_claims == 0:
            status = GroundingStatus.UNVERIFIABLE
            reason = "No extractable claims in generated answer."
            summary = None
        else:
            status = GroundingStatus.FULLY_GROUNDED
            reason = f"All {total_claims} claim(s) are fully supported by cited evidence."
            summary = None

        return GroundingResult(
            status=status,
            groundedness_score=round(score, 4),
            citation_coverage=round(citation_coverage, 4),
            claims=claims,
            citations=citations_list,
            supported_claim_count=len(supported_all),
            unsupported_claim_count=len(unsupported_all),
            partial_claim_count=len(partial_all),
            claims_with_valid_citations=claims_with_valid_cits,
            claims_without_citations=claims_without_cits,
            reason=reason,
            unsupported_claims_summary=summary,
        )

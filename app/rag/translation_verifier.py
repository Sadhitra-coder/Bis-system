"""
app/rag/translation_verifier.py

Post-translation integrity verification layer for multilingual (Hindi) answers.
Ensures that back-translating English compliance answers into Hindi (Devanagari script)
does not introduce untracked hallucinations, number modifications, dropped citations,
or altered Indian Standard numbers.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from app.grounding.validator import GroundingValidator, _DEVANAGARI_DIGITS

logger = logging.getLogger(__name__)

# Mandatory BIS Care tip sentences (English and Hindi equivalents)
_BIS_CARE_PATTERNS = [
    re.compile(r"bis\s*care", re.IGNORECASE),
    re.compile(r"बीआईएस\s*केयर", re.IGNORECASE),
    re.compile(r"authenticity of the isi mark", re.IGNORECASE),
    re.compile(r"आईएसआई\s*मार्क", re.IGNORECASE),
]

_MANDATORY_CONSUMER_TIP_EN = (
    "Consumers can verify the authenticity of the ISI mark or license validity using the official BIS Care mobile app."
)
_MANDATORY_CONSUMER_TIP_HI = (
    "उपभोक्ता आधिकारिक बीआईएस केयर मोबाइल ऐप का उपयोग करके आईएसआई मार्क की प्रामाणिकता या लाइसेंस की वैधता को सत्यापित कर सकते हैं।"
)


@dataclass
class TranslationVerificationResult:
    """Outcome of the post-translation integrity verification pass."""
    is_valid: bool
    repaired_hindi_answer: str
    issues: List[str] = field(default_factory=list)
    devanagari_ratio: float = 0.0
    missing_citations: List[str] = field(default_factory=list)
    hallucinated_citations: List[str] = field(default_factory=list)
    missing_standards: List[str] = field(default_factory=list)
    missing_numbers: List[str] = field(default_factory=list)
    clause_leaks: List[str] = field(default_factory=list)
    repaired_citations: bool = False
    repaired_clause_leaks: bool = False
    repaired_consumer_tip: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "issues": self.issues,
            "devanagari_ratio": round(self.devanagari_ratio, 4),
            "missing_citations": self.missing_citations,
            "hallucinated_citations": self.hallucinated_citations,
            "missing_standards": self.missing_standards,
            "missing_numbers": self.missing_numbers,
            "clause_leaks": self.clause_leaks,
            "repaired_citations": self.repaired_citations,
            "repaired_clause_leaks": self.repaired_clause_leaks,
            "repaired_consumer_tip": self.repaired_consumer_tip,
        }


class TranslationIntegrityVerifier:
    """
    Deterministic verifier guarding the Hindi back-translation stage.
    """

    @classmethod
    def verify_and_repair(
        cls,
        english_answer: str,
        hindi_answer: str,
        audience: str = "technical",
    ) -> TranslationVerificationResult:
        """
        Verify the Hindi translation against the grounded English answer.
        Deterministically repairs minor format/citation discrepancies.
        Flags critical numerical or standard mismatches.
        """
        issues: List[str] = []
        mode = (audience or "technical").lower().strip()
        repaired_answer = hindi_answer.strip() if hindi_answer else ""

        if not repaired_answer:
            return TranslationVerificationResult(
                is_valid=False,
                repaired_hindi_answer=english_answer,
                issues=["empty_hindi_translation"],
            )

        # ----------------------------------------------------
        # 1. DEVANAGARI SCRIPT INTEGRITY CHECK
        # ----------------------------------------------------
        devanagari_chars = len(re.findall(r"[\u0900-\u097F]", repaired_answer))
        non_space_chars = len(re.sub(r"\s+", "", repaired_answer))
        devanagari_ratio = devanagari_chars / max(non_space_chars, 1)

        # Allow Latin standard numbers and technical acronyms, but require >= 20% Devanagari script
        if devanagari_ratio < 0.20 and devanagari_chars < 15:
            issues.append(f"insufficient_devanagari_script:ratio={devanagari_ratio:.2f}")

        # ----------------------------------------------------
        # 2. CITATION TOKEN INVARIANCE (e.g. [EV1], [EV2])
        # ----------------------------------------------------
        en_cits = [c.upper() for c in re.findall(r"\[(EV\d+|citation-\d+)\]", english_answer, re.IGNORECASE)]
        unique_en_cits = list(dict.fromkeys(en_cits))

        hi_cits = [c.upper() for c in re.findall(r"\[(EV\d+|citation-\d+)\]", repaired_answer, re.IGNORECASE)]
        unique_hi_cits = list(dict.fromkeys(hi_cits))

        # Check for hallucinated citation tokens in Hindi
        hallucinated_cits = [c for c in unique_hi_cits if c not in unique_en_cits]
        if hallucinated_cits:
            issues.append(f"hallucinated_citation_tokens:{','.join(hallucinated_cits)}")

        # Check for dropped citation tokens
        missing_cits = [c for c in unique_en_cits if c not in unique_hi_cits]
        repaired_cits = False

        if missing_cits and unique_en_cits:
            # Deterministic Auto-Repair: Append or normalize separated Sources line
            # Strip any corrupted or partial sources line
            repaired_answer = re.sub(
                r"\n*(?:\*\*Sources:\*\*|Sources:|References:|स्रोत:|संदर्भ:).*$",
                "",
                repaired_answer,
                flags=re.DOTALL | re.IGNORECASE
            ).strip()

            cits_formatted = ", ".join(f"[{c}]" for c in unique_en_cits)
            repaired_answer = f"{repaired_answer}\n\nSources: {cits_formatted}"
            repaired_cits = True
            logger.info("Restored missing citation line to Hindi answer: %s", cits_formatted)

        # ----------------------------------------------------
        # 3. INDIAN STANDARD NUMBER INVARIANCE (e.g. IS 1293)
        # ----------------------------------------------------
        # Find all distinct standard numbers in English: e.g. "IS 1293", "IS 694"
        en_standards = [
            f"IS {num}" for num in re.findall(r"\bIS\s*([1-9]\d{1,5})\b", english_answer, re.IGNORECASE)
        ]
        unique_en_stds = list(dict.fromkeys(en_standards))

        missing_stds = []
        for std in unique_en_stds:
            std_clean = std.replace(" ", r"\s*")
            # Match either English "IS 1293" or transliterated "आईएस 1293" / "आई.एस. 1293"
            pattern = re.compile(rf"\b(?:{std_clean}|आईएस\s*{std.split()[1]}|आई\.एस\.\s*{std.split()[1]})\b", re.IGNORECASE)
            if not pattern.search(repaired_answer):
                missing_stds.append(std)

        if missing_stds:
            issues.append(f"missing_standard_numbers:{','.join(missing_stds)}")

        # ----------------------------------------------------
        # 4. NUMERICAL VALUE & MEASUREMENT INVARIANCE
        # ----------------------------------------------------
        en_quantities = GroundingValidator.extract_numbers_and_quantities(english_answer)
        # Normalize Hindi answer digits for comparison
        norm_hi_text = repaired_answer.translate(_DEVANAGARI_DIGITS)

        missing_nums = []
        for qty in en_quantities:
            qty_clean = qty.lower().replace(",", "")
            parts = qty_clean.split()
            bare_num = parts[0] if parts else qty_clean
            # Verify the bare number exists in the translated text
            num_pat = re.compile(rf"\b{re.escape(bare_num)}\b")
            if not num_pat.search(norm_hi_text):
                missing_nums.append(qty)

        if missing_nums:
            issues.append(f"missing_numerical_values:{','.join(missing_nums)}")

        # ----------------------------------------------------
        # 5. AUDIENCE CLAUSE SUPPRESSION / RETENTION
        # ----------------------------------------------------
        clause_leaks: List[str] = []
        repaired_clause_leaks = False
        if mode == "consumer":
            # In consumer mode, clause numbers MUST NOT leak
            leaks = re.findall(r"\b(?:Clause|खंड|धारा)\s*\d+(?:\.\d+)*\b", repaired_answer, re.IGNORECASE)
            if leaks:
                clause_leaks = list(dict.fromkeys(leaks))
                # Deterministic Auto-Repair: strip leaked clause references in consumer mode
                repaired_answer = re.sub(
                    r"\s*\b(?:Clause|खंड|धारा)\s*\d+(?:\.\d+)*\b",
                    "",
                    repaired_answer,
                    flags=re.IGNORECASE
                )
                repaired_clause_leaks = True
                logger.info("Stripped leaked clause references from consumer Hindi answer: %s", clause_leaks)

            # Check BIS Care mobile app pointer in consumer mode
            has_bis_care = any(p.search(repaired_answer) for p in _BIS_CARE_PATTERNS)
            repaired_consumer_tip = False
            if not has_bis_care:
                # Insert the official Hindi BIS Care tip before the Sources line
                sources_match = re.search(r"(?:\n\n|\n)(?:Sources:|References:|स्रोत:|संदर्भ:)", repaired_answer, re.IGNORECASE)
                if sources_match:
                    idx = sources_match.start()
                    repaired_answer = f"{repaired_answer[:idx]}\n\n{_MANDATORY_CONSUMER_TIP_HI}{repaired_answer[idx:]}"
                else:
                    repaired_answer = f"{repaired_answer}\n\n{_MANDATORY_CONSUMER_TIP_HI}"
                repaired_consumer_tip = True
                logger.info("Inserted official Hindi BIS Care verification tip into consumer response.")

        # ----------------------------------------------------
        # 6. 4-PART VISUAL HIERARCHY INTEGRITY
        # ----------------------------------------------------
        # Bold headline check
        has_bold_headline = bool(re.match(r"^\s*\*\*[^*]+\*\*", repaired_answer))
        if not has_bold_headline:
            # If English started with a bold headline, try to preserve it
            en_headline_match = re.match(r"^\s*(\*\*[^*]+\*\*)\s*\n", english_answer)
            if en_headline_match:
                # Prepend the bold headline
                repaired_answer = f"{en_headline_match.group(1)}\n\n{repaired_answer.strip()}"
                logger.info("Preserved bold headline in Hindi answer.")

        # Final validity determination:
        # A translation is invalid if:
        # - Devanagari script is completely absent (<20% and <15 chars)
        # - Critical standard numbers were lost
        # - Critical numbers/tolerances were modified or lost
        # - Hallucinated citation tokens were introduced
        critical_issues = [
            iss for iss in issues
            if iss.startswith("insufficient_devanagari_script")
            or iss.startswith("missing_standard_numbers")
            or iss.startswith("missing_numerical_values")
            or iss.startswith("hallucinated_citation_tokens")
        ]

        is_valid = len(critical_issues) == 0

        return TranslationVerificationResult(
            is_valid=is_valid,
            repaired_hindi_answer=repaired_answer,
            issues=issues,
            devanagari_ratio=devanagari_ratio,
            missing_citations=missing_cits,
            hallucinated_citations=hallucinated_cits,
            missing_standards=missing_stds,
            missing_numbers=missing_nums,
            clause_leaks=clause_leaks,
            repaired_citations=repaired_cits,
            repaired_clause_leaks=repaired_clause_leaks,
            repaired_consumer_tip=repaired_consumer_tip if mode == "consumer" else False,
        )

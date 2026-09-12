"""
app/document_intelligence/classifier.py

Deterministic Document Classifier for Compliance & Engineering Documents (Phase 14).

DESIGN PRINCIPLES:
  - Classifies documents into canonical DocumentType taxonomy:
    TEST_REPORT, CALIBRATION_REPORT, CERTIFICATE, DECLARATION,
    MANUAL, TECHNICAL_SPECIFICATION, DRAWING, QUALITY_RECORD,
    LAB_REPORT, OTHER.
  - Deterministic keyword and structural scoring with filename consideration.
  - Content indicators outweigh filenames: adversarial filenames
    (e.g., naming a user manual 'test_report.pdf') are overridden by actual content.
  - Confidence scoring reflects signal strength and margin over runner-up.
"""

from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from app.document_intelligence.models import DocumentType


# Keyword signatures for each document category
CLASSIFICATION_RULES: Dict[DocumentType, Dict[str, Any]] = {
    DocumentType.CALIBRATION_REPORT: {
        "keywords": [
            "calibration certificate", "calibration report", "calibrated on",
            "calibration due", "traceability", "standard used for calibration",
            "calibrated by", "uncertainty of measurement", "calibration equipment",
        ],
        "filename_patterns": [r"calib", r"calibration"],
        "weight": 1.2,
    },
    DocumentType.DECLARATION: {
        "keywords": [
            "declaration of conformity", "manufacturer declaration",
            "self-declaration", "supplier declaration", "we hereby declare",
            "declare under our sole responsibility", "eu declaration",
        ],
        "filename_patterns": [r"declaration", r"conformity_dec", r"self_dec"],
        "weight": 1.2,
    },
    DocumentType.CERTIFICATE: {
        "keywords": [
            "certificate of conformity", "compliance certificate", "bis licence",
            "isi mark certificate", "iso 9001", "certification", "valid up to",
            "certified that", "certification mark", "license number",
        ],
        "filename_patterns": [r"certificate", r"cert_", r"licence", r"license"],
        "weight": 1.0,
    },
    DocumentType.TEST_REPORT: {
        "keywords": [
            "test report", "test certificate", "testing laboratory", "test laboratory",
            "sample description", "test results", "test result", "test method",
            "nabl accredited", "nabl", "sample tested", "date of testing",
            "specified requirement", "observed value", "test parameter", "pass/fail",
            "result: pass", "result: fail",
        ],
        "filename_patterns": [r"test_report", r"test_cert", r"test_result", r"testing"],
        "weight": 1.0,
    },
    DocumentType.LAB_REPORT: {
        "keywords": [
            "laboratory analysis report", "chemical analysis report", "test analysis",
            "analytical report", "spectroscopy", "metallurgical analysis",
            "chemical composition", "chromatography", "microstructure",
        ],
        "filename_patterns": [r"lab_report", r"chemical_analysis", r"spectro"],
        "weight": 1.0,
    },
    DocumentType.QUALITY_RECORD: {
        "keywords": [
            "quality inspection report", "factory inspection", "quality plan", "qap",
            "incoming inspection", "batch inspection", "qc report", "inspection report",
            "pre-dispatch inspection", "quality assurance",
        ],
        "filename_patterns": [r"qap", r"qc_report", r"inspection", r"quality_record"],
        "weight": 1.0,
    },
    DocumentType.DRAWING: {
        "keywords": [
            "engineering drawing", "schematic diagram", "circuit diagram",
            "assembly drawing", "ga drawing", "scale:", "all dimensions in mm",
            "drawing no", "drawn by", "checked by", "projection:",
        ],
        "filename_patterns": [r"drawing", r"schematic", r"cad_", r"diagram"],
        "weight": 1.1,
    },
    DocumentType.MANUAL: {
        "keywords": [
            "user manual", "operating instructions", "instruction manual",
            "installation guide", "user guide", "maintenance instructions",
            "safety instructions", "troubleshooting", "how to operate",
        ],
        "filename_patterns": [r"manual", r"user_guide", r"instructions", r"install_guide"],
        "weight": 1.0,
    },
    DocumentType.TECHNICAL_SPECIFICATION: {
        "keywords": [
            "technical specification", "data sheet", "datasheet", "product specification",
            "spec sheet", "rated voltage", "rated power", "technical data",
            "operating temperature", "electrical characteristics",
        ],
        "filename_patterns": [r"datasheet", r"tech_spec", r"spec_sheet", r"data_sheet"],
        "weight": 0.9,
    },
}


def classify_document(
    text: str,
    filename: Optional[str] = None
) -> Tuple[DocumentType, float, Dict[str, Any]]:
    """
    Deterministically classifies a document based on text content and optional filename.

    Returns:
      (document_type, confidence_score, explanation_dict)
    """
    text_lower = (text or "").lower()
    fname_lower = (Path(filename).name.lower() if filename else "")

    scores: Dict[DocumentType, float] = {dt: 0.0 for dt in CLASSIFICATION_RULES.keys()}
    evidence_matches: Dict[str, List[str]] = {}

    for doc_type, rules in CLASSIFICATION_RULES.items():
        doc_score = 0.0
        matched_kw: List[str] = []

        # Content keyword matching (primary signal)
        for kw in rules["keywords"]:
            if kw in text_lower:
                # Count occurrences up to 3 to reward strong presence without runaway
                count = min(text_lower.count(kw), 3)
                doc_score += count * 2.5 * rules["weight"]
                matched_kw.append(kw)

        # Filename heuristic (secondary signal)
        if fname_lower:
            for pat in rules["filename_patterns"]:
                if re.search(pat, fname_lower):
                    doc_score += 1.5 * rules["weight"]
                    matched_kw.append(f"filename:{pat}")
                    break

        scores[doc_type] = doc_score
        if matched_kw:
            evidence_matches[doc_type.value] = matched_kw

    # Sort candidates by score
    sorted_candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_type, best_score = sorted_candidates[0]
    second_type, second_score = sorted_candidates[1]

    # If top score is negligible, fall back to OTHER
    if best_score < 2.0:
        return (
            DocumentType.OTHER,
            0.20,
            {
                "reason": "Insufficient distinctive compliance or engineering document signals.",
                "scores": {k.value: round(v, 2) for k, v in scores.items() if v > 0},
                "matched_indicators": evidence_matches,
            }
        )

    # Calculate confidence margin
    margin = (best_score - second_score) / max(best_score, 1.0)
    # Base confidence ranges from 0.55 to 0.98
    confidence = min(0.98, max(0.55, 0.60 + 0.35 * margin))

    # Anti-spoofing check: If filename suggested something else with high weight,
    # note that content signals prevailed
    override_note = None
    if fname_lower:
        for doc_type, rules in CLASSIFICATION_RULES.items():
            if doc_type != best_type:
                for pat in rules["filename_patterns"]:
                    if re.search(pat, fname_lower) and scores[doc_type] < best_score:
                        override_note = (
                            f"Filename suggested {doc_type.value} but text content "
                            f"strictly demonstrated {best_type.value} characteristics."
                        )
                        break

    explanation = {
        "best_type": best_type.value,
        "score": round(best_score, 2),
        "runner_up": second_type.value if second_score > 0 else None,
        "runner_up_score": round(second_score, 2),
        "matched_indicators": evidence_matches.get(best_type.value, []),
        "anti_spoofing_note": override_note,
    }

    return (best_type, round(confidence, 2), explanation)

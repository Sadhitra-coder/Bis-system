"""
app/schemes/selector.py

Certification Scheme Guidance / Scheme Selector (PRD R3).
Determines the applicable BIS certification scheme based on product/standard,
mandatory QCO status, and manufacturer origin.

Utilizes reference schemes seeded in the `certification_schemes` SQLite table:
- Scheme I: Standard Mark (ISI Mark)
- Scheme II: Compulsory Registration Scheme (CRS)
- Scheme IV: Hallmarking of Precious Metals
- Scheme X: Foreign Manufacturers Certification Scheme (FMCS)
"""

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SchemeRecommendation:
    scheme_code: str
    scheme_name: str
    is_mandatory: bool
    qco_number: Optional[str]
    qco_title: Optional[str]
    origin: str
    headline: str
    explanation: str
    details: List[str]
    sources: List[str]
    raw_scheme_info: Dict[str, Any] = field(default_factory=dict)

    def to_formatted_answer(self) -> str:
        """
        Format the scheme recommendation into the canonical 4-part visual hierarchy:
        1. Bold headline / status line
        2. 2-4 sentences plain explanation
        3. Clean bullet list of key parameters
        4. Separated Sources line at the end
        """
        bullets = "\n".join(f"- {d}" for d in self.details)
        sources_line = f"Sources: {', '.join(self.sources)}" if self.sources else "Sources: [EV1]"
        return f"{self.headline}\n\n{self.explanation}\n\n{bullets}\n\n{sources_line}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scheme_code": self.scheme_code,
            "scheme_name": self.scheme_name,
            "is_mandatory": self.is_mandatory,
            "qco_number": self.qco_number,
            "qco_title": self.qco_title,
            "origin": self.origin,
            "headline": self.headline,
            "explanation": self.explanation,
            "details": self.details,
            "sources": self.sources,
            "raw_scheme_info": self.raw_scheme_info,
        }


def _get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def select_certification_scheme(
    standard_number: Optional[str] = None,
    product_description: Optional[str] = None,
    is_mandatory: Optional[bool] = None,
    qco_number: Optional[str] = None,
    manufacturer_origin: str = "domestic",
    knowledge_repo: Optional[Any] = None,
) -> SchemeRecommendation:
    """
    Selects the governing BIS certification scheme based on standard/product context,
    statutory QCO status, and manufacturer geographic origin.

    Logic:
    - Gold / jewellery (IS 1417, IS 15820) -> Scheme IV (Hallmarking Scheme for Precious Metals).
    - Foreign origin + mandatory QCO -> Scheme X (FMCS - Foreign Manufacturers Certification Scheme).
    - Mandatory + Electronics / IT -> Scheme II (CRS - Compulsory Registration Scheme).
    - Mandatory + General Industrial / Toys / Appliances -> Scheme I (ISI Mark / Standard Mark License).
    - Non-mandatory / No QCO -> Voluntary certification under Scheme I with explanation.
    """
    origin = (manufacturer_origin or "domestic").strip().lower()
    is_foreign = origin in ("foreign", "overseas", "international")

    # Normalize standard number
    std_clean = ""
    std_num_only = ""
    if standard_number:
        m = re.search(r"IS\s*([0-9]+(?:\s*(?:Part|Pt\.?)\s*[0-9]+)?)", standard_number, re.IGNORECASE)
        if m:
            std_clean = f"IS {m.group(1)}"
            num_m = re.search(r"\d+", m.group(1))
            std_num_only = num_m.group(0) if num_m else ""
        else:
            std_clean = standard_number.strip()
            num_m = re.search(r"\d+", std_clean)
            std_num_only = num_m.group(0) if num_m else ""

    # Look up metadata from SQLite
    catalog_record = None
    qco_record = None
    all_schemes = {}

    try:
        conn = _get_db_connection()
        c = conn.cursor()

        # Load certification schemes
        for r in c.execute("SELECT scheme_id, scheme_code, scheme_name, description FROM certification_schemes").fetchall():
            all_schemes[r["scheme_code"]] = dict(r)
            all_schemes[r["scheme_id"]] = dict(r)

        # Lookup in standards_metadata_catalog
        if std_clean or std_num_only:
            query = "SELECT * FROM standards_metadata_catalog WHERE standard_number LIKE ? OR standard_number LIKE ?"
            row = c.execute(query, (f"%{std_clean}%", f"%{std_num_only}%")).fetchone()
            if row:
                catalog_record = dict(row)

        # Lookup matching QCO if not explicitly provided
        if not qco_number and (std_clean or std_num_only):
            # Check standards_metadata_catalog first
            if catalog_record and catalog_record.get("mandatory_qco_number"):
                qco_number = catalog_record.get("mandatory_qco_number")

            # Check qcos table by keyword or standard
            if not qco_number:
                row_qco = c.execute(
                    "SELECT * FROM qcos WHERE title LIKE ? OR qco_number LIKE ?",
                    (f"%{std_num_only}%", f"%{std_num_only}%")
                ).fetchone()
                if row_qco:
                    qco_record = dict(row_qco)
                    qco_number = qco_record["qco_number"]

        elif qco_number:
            row_qco = c.execute(
                "SELECT * FROM qcos WHERE qco_number LIKE ? OR title LIKE ?",
                (f"%{qco_number}%", f"%{qco_number}%")
            ).fetchone()
            if row_qco:
                qco_record = dict(row_qco)

        conn.close()
    except Exception as e:
        logger.warning("Error fetching scheme metadata: %s", e)

    # Determine mandatory status from catalog or param
    effective_mandatory = False
    if is_mandatory is not None:
        effective_mandatory = bool(is_mandatory)
    elif catalog_record:
        effective_mandatory = bool(catalog_record.get("is_mandatory"))
    elif qco_record:
        effective_mandatory = bool(qco_record.get("is_mandatory", 1))

    effective_qco_number = qco_number or (catalog_record.get("mandatory_qco_number") if catalog_record else None)
    effective_qco_title = qco_record.get("title") if qco_record else (effective_qco_number or None)

    # Context text for keyword matching
    context_text = f"{standard_number or ''} {product_description or ''} {catalog_record.get('title', '') if catalog_record else ''} {effective_qco_title or ''}".lower()

    # 1. Gold / Jewellery / Precious metals -> Scheme IV (Hallmarking)
    is_hallmarking = (
        "1417" in std_num_only
        or "15820" in std_num_only
        or any(k in context_text for k in ["gold", "jewellery", "jewelry", "hallmark", "artefact", "silver"])
    )
    if is_hallmarking:
        scheme_info = all_schemes.get("SCHEME-IV") or all_schemes.get("HALLMARKING") or all_schemes.get("scheme_hallmarking", {})
        headline = "**Recommended Scheme: Scheme IV (Hallmarking of Precious Metals)**"
        explanation = (
            f"Precious metals including gold jewellery and artefacts under {std_clean or 'IS 1417'} are governed by the statutory "
            f"Hallmarking Scheme (Scheme IV) under Section 14 and 16 of the Bureau of Indian Standards Act, 2016. "
            f"Certification is mandatory pursuant to the Gold and Gold Alloys Hallmarking Order, 2020. "
            f"All articles must be certified and laser-marked with a 6-digit alphanumeric Hallmark Unique Identification (HUID)."
        )
        details = [
            "Applicable Scheme: Scheme IV - Hallmarking Scheme for Precious Metals",
            f"Governing Standard: {std_clean or 'IS 1417:2016'} (Gold and Gold Alloys, Jewellery/Artefacts - Fineness and Marking)",
            f"Statutory Order: {effective_qco_number or 'S.O. 4345(E) / Hallmarking Order, 2020'} (Mandatory)",
            "Marking Requirements: BIS Standard Logo, Purity in Karat and Fineness (e.g., 22K916), Assaying & Hallmarking Centre (AHC) mark, and 6-digit HUID",
            "Manufacturer Scope: Registered jewellers selling to domestic consumers; testing is performed via authorized AHC assay centers."
        ]
        return SchemeRecommendation(
            scheme_code="SCHEME-IV",
            scheme_name="Scheme IV - Hallmarking Scheme for Precious Metals",
            is_mandatory=True,
            qco_number=effective_qco_number or "S.O. 4345(E)",
            qco_title=effective_qco_title or "Gold and Gold Alloys Hallmarking Order, 2020",
            origin=origin,
            headline=headline,
            explanation=explanation,
            details=details,
            sources=["[EV1]"],
            raw_scheme_info=scheme_info,
        )

    # 2. Foreign Manufacturer + Mandatory Product -> Scheme X (FMCS)
    if is_foreign and (effective_mandatory or effective_qco_number):
        scheme_info = all_schemes.get("SCHEME-X") or all_schemes.get("scheme_10_fmcs", {})
        headline = "**Recommended Scheme: Scheme X - Foreign Manufacturers Certification Scheme (FMCS)**"
        explanation = (
            f"Overseas manufacturers exporting products under mandatory Indian Standards (such as {std_clean or 'this standard'}) "
            f"must obtain certification under Scheme X (FMCS) pursuant to Section 13(1) of the BIS Act, 2016 and the "
            f"BIS (Conformity Assessment) Regulations, 2018. Under this scheme, BIS grants a license to use the Standard Mark (ISI) "
            f"following an on-site audit of overseas manufacturing and testing facilities."
        )
        details = [
            "Applicable Scheme: Scheme X - Foreign Manufacturers Certification Scheme (FMCS)",
            f"Target Standard: {std_clean or 'Notified Indian Standard'}",
            f"Mandatory Order: {effective_qco_number or 'Applicable Quality Control Order (QCO)'} (Enforced at Indian Customs)",
            "Authorized Indian Representative (AIR): Foreign applicants must designate a resident Authorized Indian Representative responsible for statutory compliance.",
            "Verification Procedure: Satisfactory factory inspection by BIS auditor, independent sample drawing, and compliance verification in India."
        ]
        return SchemeRecommendation(
            scheme_code="SCHEME-X",
            scheme_name="Scheme X - Foreign Manufacturers Certification Scheme (FMCS)",
            is_mandatory=True,
            qco_number=effective_qco_number,
            qco_title=effective_qco_title,
            origin=origin,
            headline=headline,
            explanation=explanation,
            details=details,
            sources=["[EV1]"],
            raw_scheme_info=scheme_info,
        )

    # 3. Electronics / IT Goods -> Scheme II (Compulsory Registration Scheme - CRS)
    is_electronics_cro = any(k in context_text for k in [
        "cro", "crs", "compulsory registration", "it equipment", "smart meter", "led driver",
        "power bank", "adapter", "solar", "photovoltaic", "13252", "15885", "16102", "14286"
    ])
    if effective_mandatory and is_electronics_cro:
        scheme_info = all_schemes.get("SCHEME-II") or all_schemes.get("SCHEME_2") or all_schemes.get("scheme_2_crs", {})
        headline = "**Recommended Scheme: Scheme II - Compulsory Registration Scheme (CRS)**"
        explanation = (
            f"Products covered under Electronics and IT Goods orders (such as {std_clean or 'notified IT/electronics'}) "
            f"are governed by Scheme II (Compulsory Registration Scheme - CRS) under the BIS (Conformity Assessment) Regulations, 2018. "
            f"Under CRS, manufacturers obtain registration through self-declaration of conformity based on laboratory test reports "
            f"issued by BIS-recognized testing laboratories."
        )
        details = [
            "Applicable Scheme: Scheme II - Compulsory Registration Scheme (CRS)",
            f"Governing Standard: {std_clean or 'Electronic / IT Standard'}",
            f"Notifying Authority & Order: {effective_qco_number or 'MeitY Compulsory Registration Order (CRO)'}",
            "Conformity Route: Self-declaration of conformity backed by mandatory product type testing at a BIS-recognized laboratory.",
            "Pre-grant Inspection: Pre-grant factory audit is not required under Scheme II; registration is granted on basis of valid test reports."
        ]
        return SchemeRecommendation(
            scheme_code="SCHEME-II",
            scheme_name="Scheme II - Compulsory Registration Scheme (CRS)",
            is_mandatory=True,
            qco_number=effective_qco_number,
            qco_title=effective_qco_title,
            origin=origin,
            headline=headline,
            explanation=explanation,
            details=details,
            sources=["[EV1]"],
            raw_scheme_info=scheme_info,
        )

    # 4. Mandatory Product under QCO (e.g. Toys IS 9873, Plugs IS 1293, Cables IS 694, Steel IS 1786) -> Scheme I (ISI)
    if effective_mandatory or effective_qco_number:
        scheme_info = all_schemes.get("SCHEME-I") or all_schemes.get("SCHEME_1") or all_schemes.get("scheme_1_isi", {})
        headline = "**Recommended Scheme: Scheme I - Standard Mark (ISI Mark Certification)**"
        explanation = (
            f"Products conforming to {std_clean or 'this standard'} are covered under a mandatory Quality Control Order (QCO) "
            f"issued by the competent Central Ministry. Manufacturing, importing, distributing, or selling this product without a valid "
            f"BIS license is prohibited under Section 16 of the BIS Act, 2016. Certification must be secured under Scheme I (Standard Mark / ISI Mark)."
        )
        details = [
            "Applicable Scheme: Scheme I - Standard Mark (ISI Mark License)",
            f"Governing Standard: {std_clean or 'Mandatory Indian Standard'}",
            f"Statutory Order: {effective_qco_number or 'Quality Control Order (QCO)'} ({effective_qco_title or 'Mandatory'})",
            "License Process: Requires complete factory quality control audit, verified in-house test infrastructure, and conforming laboratory test reports.",
            "Surveillance: Regular factory surveillance audits and random market sampling are mandated to maintain license validity."
        ]
        return SchemeRecommendation(
            scheme_code="SCHEME-I",
            scheme_name="Scheme I - Standard Mark (ISI Mark)",
            is_mandatory=True,
            qco_number=effective_qco_number,
            qco_title=effective_qco_title,
            origin=origin,
            headline=headline,
            explanation=explanation,
            details=details,
            sources=["[EV1]"],
            raw_scheme_info=scheme_info,
        )

    # 5. Non-Mandatory / No QCO -> Scheme I (Standard Mark / Verification Advised)
    scheme_info = all_schemes.get("SCHEME-I") or all_schemes.get("SCHEME_1") or all_schemes.get("scheme_1_isi", {})
    headline = "**Recommended Scheme: Scheme I - Standard Mark (Voluntary / Verification Required)**"
    explanation = (
        f"Standard {std_clean or 'this specification'} is not listed under active mandatory Quality Control Orders (QCOs) "
        f"in the indexed local regulatory records. Where no mandatory Central Government QCO applies, certification operates under "
        f"voluntary Scheme I. However, statutory mandate status must be verified against current Gazette of India notifications, "
        f"as new QCOs are continuously gazetted by Central Ministries under Section 16 of the BIS Act, 2016."
    )
    details = [
        "Applicable Scheme: Scheme I - Standard Mark (Conformity Assessment)",
        f"Governing Standard: {std_clean or 'Indian Standard'}",
        "Statutory Mandate: No mandatory QCO identified in indexed records; independent gazette verification advised.",
        "Business Rationale: Demonstrates compliance with national quality benchmarks, enhances consumer trust, and satisfies institutional procurement criteria.",
        "Application Route: Manufacturers can apply via the Manakonline portal for an ISI Mark license under Scheme I."
    ]
    return SchemeRecommendation(
        scheme_code="SCHEME-I-VOLUNTARY",
        scheme_name="Scheme I - Standard Mark (Voluntary / Verification Advised)",
        is_mandatory=False,
        qco_number=None,
        qco_title=None,
        origin=origin,
        headline=headline,
        explanation=explanation,
        details=details,
        sources=["[EV1]"],
        raw_scheme_info=scheme_info,
    )

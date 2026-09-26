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



def _match_product_catalog_record(
    c: sqlite3.Cursor,
    product_description: Optional[str],
) -> Optional[Dict[str, Any]]:
    """
    Identifies a matching standard record from standards_metadata_catalog based
    on product description text. Never invents standards.
    """
    if not product_description:
        return None
    desc = product_description.lower()

    rows = c.execute("SELECT * FROM standards_metadata_catalog").fetchall()

    # Domain keyword rules with exact standard mappings
    keyword_boosts = [
        (["cable", "cables", "wire", "wires", "cord", "cords", "flexible cord", "pvc cable"], "IS 694"),
        (["smart meter", "smart meters", "static meter", "watt-hour meter", "electricity meter"], "IS 16444"),
        (["toy", "toys", "plaything", "doll"], "IS 9873"),
        (["plug", "plugs", "socket", "sockets", "socket-outlet", "adaptor"], "IS 1293"),
        (["steel bar", "steel bars", "rebar", "rebars", "tmt bar", "tmt bars", "deformed steel"], "IS 1786"),
        (["structural steel", "steel plate", "steel beam"], "IS 2062"),
        (["thermometer", "clinical thermometer", "mercury thermometer"], "IS 3055"),
        (["gold", "jewellery", "jewelry", "hallmark", "artefact", "silver"], "IS 1417"),
        (["ppc cement", "portland pozzolana", "flyash cement"], "IS 1489"),
        (["opc cement", "ordinary portland cement"], "IS 269"),
        (["surgical glove", "rubber glove", "medical glove", "latex glove"], "IS 13422"),
        (["hdpe pipe", "pe pipe", "polyethylene pipe"], "IS 4984"),
        (["solar panel", "solar pv", "photovoltaic", "pv module"], "IS 14286"),
        (["led driver", "led controlgear", "electronic controlgear"], "IS 15885"),
        (["led lamp", "led bulb", "self-ballasted led"], "IS 16102"),
        (["laptop", "tablet", "it equipment", "server", "power adapter"], "IS 13252"),
        (["safety footwear", "safety shoe", "safety boot"], "IS 17043"),
        (["sports footwear", "athletic footwear", "sports shoe"], "IS 15844"),
        (["packaged drinking water", "bottled water", "water jar"], "IS 14543"),
    ]

    for synonyms, preferred_std in keyword_boosts:
        if any(re.search(r"\b" + re.escape(syn) + r"\b", desc) for syn in synonyms):
            for r in rows:
                if preferred_std in r["standard_number"]:
                    return dict(r)

    # General fallback: check applicable_products JSON, title, category
    best_record = None
    best_score = 0
    for r in rows:
        row_dict = dict(r)
        score = 0
        prods = []
        if row_dict.get("applicable_products"):
            try:
                prods = json.loads(row_dict["applicable_products"])
            except Exception:
                pass

        for p in prods:
            p_clean = p.lower()
            if p_clean in desc:
                score += 4
            elif any(w in desc for w in p_clean.split() if len(w) > 3):
                score += 2

        title_lower = (row_dict.get("title") or "").lower()
        for w in title_lower.split():
            if len(w) > 4 and w in desc:
                score += 1

        cat_lower = (row_dict.get("category") or "").lower()
        for w in cat_lower.split():
            if len(w) > 4 and w in desc:
                score += 1

        if score > best_score:
            best_score = score
            best_record = row_dict

    if best_score >= 3:
        return best_record
    return None


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

    Strict regulatory decision path:
    1. Identify product / standard (never hallucinate standards or invent fake QCOs).
    2. Establish regulatory applicability & applicable QCO.
    3. Route according to authoritative statutory regime:
       - Gold / jewellery -> Scheme IV (Hallmarking Scheme).
       - Electronics / IT goods under MeitY CRO -> Scheme II (CRS) for both domestic and foreign.
       - Mandatory products under Central Ministry QCO for Standard Mark (ISI) ->
           * Foreign origin: Scheme X (FMCS - Foreign Manufacturers Certification Scheme).
           * Domestic origin: Scheme I (Standard Mark / ISI Mark License).
       - Voluntary / Non-mandatory products -> Scheme I (or Scheme X for overseas voluntary).
       - Unspecified product / unknown standard -> Clarification required with explicit uncertainty.
    """
    origin = (manufacturer_origin or "domestic").strip().lower()
    is_foreign = origin in ("foreign", "overseas", "international")

    # Detect foreign origin from product description / query text if default domestic
    if not is_foreign and product_description:
        desc_lower = product_description.lower()
        foreign_keywords = [
            "foreign", "overseas", "international", "abroad", "outside india",
            "export to india", "exporting to india", "exporting cables to india",
            "import to india", "importing to india", "imported", "importer",
            "non-domestic", "foreign factory", "overseas factory", "foreign manufacturer"
        ]
        if any(k in desc_lower for k in foreign_keywords):
            is_foreign = True
            origin = "foreign"

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

        # Guard against contradictory standard assignment
        # If standard_number was passed (e.g. from an ungrounded retrieval chunk like IS 16444 smart meters),
        # but product_description explicitly asks about another product (e.g. cables), resolve by product!
        if product_description:
            matched_by_prod = _match_product_catalog_record(c, product_description)
            if matched_by_prod:
                # If no standard was provided, or if the provided standard conflicts with explicit product
                if not std_clean:
                    catalog_record = matched_by_prod
                    std_clean = matched_by_prod["standard_number"].split(":")[0]
                    num_m = re.search(r"\d+", std_clean)
                    std_num_only = num_m.group(0) if num_m else ""
                elif "16444" in std_clean and not any(k in product_description.lower() for k in ["meter", "watt-hour", "static meter"]):
                    logger.info(
                        "Standard %s conflicts with product description '%s'. Re-aligning to %s.",
                        std_clean, product_description, matched_by_prod["standard_number"]
                    )
                    catalog_record = matched_by_prod
                    std_clean = matched_by_prod["standard_number"].split(":")[0]
                    num_m = re.search(r"\d+", std_clean)
                    std_num_only = num_m.group(0) if num_m else ""

        # Lookup in standards_metadata_catalog by standard number if not resolved by product
        if not catalog_record and (std_clean or std_num_only):
            query_sql = "SELECT * FROM standards_metadata_catalog WHERE standard_number LIKE ? OR standard_number LIKE ?"
            row = c.execute(query_sql, (f"%{std_clean}%", f"%{std_num_only}%")).fetchone()
            if row:
                catalog_record = dict(row)

        # Lookup matching QCO if not explicitly provided
        if not qco_number and (std_clean or std_num_only):
            # Check standards_metadata_catalog first
            if catalog_record and catalog_record.get("mandatory_qco_number"):
                qco_number = catalog_record.get("mandatory_qco_number")

            # Check qcos table by keyword, qco title, or standard number
            if not qco_number:
                row_qco = c.execute(
                    "SELECT * FROM qcos WHERE title LIKE ? OR qco_number LIKE ?",
                    (f"%{std_num_only}%", f"%{std_num_only}%")
                ).fetchone()
                if row_qco:
                    qco_record = dict(row_qco)
                    qco_number = qco_record["qco_number"]

        # If qco_number was resolved from catalog (e.g. 'Electrical Wires, Cables and Cords QCO 2024')
        if qco_number and not qco_record:
            row_qco = c.execute(
                "SELECT * FROM qcos WHERE qco_number LIKE ? OR title LIKE ?",
                (f"%{qco_number}%", f"%{qco_number}%")
            ).fetchone()
            if row_qco:
                qco_record = dict(row_qco)
            else:
                # Try matching by standard number or product keywords in qcos
                if std_num_only:
                    row_qco = c.execute("SELECT * FROM qcos WHERE title LIKE ?", (f"%{std_num_only}%",)).fetchone()
                    if row_qco:
                        qco_record = dict(row_qco)

        conn.close()
    except Exception as e:
        logger.warning("Error fetching scheme metadata: %s", e)

    # Determine mandatory status from catalog or parameter
    effective_mandatory = False
    if is_mandatory is not None:
        effective_mandatory = bool(is_mandatory)
    elif catalog_record:
        effective_mandatory = bool(catalog_record.get("is_mandatory"))
    elif qco_record:
        effective_mandatory = bool(qco_record.get("is_mandatory", 1))

    effective_qco_number = qco_record.get("qco_number") if qco_record else (qco_number or (catalog_record.get("mandatory_qco_number") if catalog_record else None))
    effective_qco_title = qco_record.get("title") if qco_record else (catalog_record.get("mandatory_qco_number") if catalog_record else effective_qco_number)

    # Normalized standard display label
    std_display = catalog_record.get("standard_number") if catalog_record else (std_clean or "Indian Standard")
    std_title = catalog_record.get("title") if catalog_record else ""

    # Context text for category matching
    context_text = f"{std_clean} {std_title} {product_description or ''} {effective_qco_title or ''}".lower()

    # -------------------------------------------------------------------
    # STEP A: PRODUCT / STANDARD UNSPECIFIED -> DO NOT INVENT A STANDARD OR CLAIM FAKE QCO
    # -------------------------------------------------------------------
    if not catalog_record and not std_clean:
        if is_foreign:
            headline = "**BIS Certification Scheme Guidance: Clarification Required (Foreign Manufacturer / Scheme X Overview)**"
            explanation = (
                "For overseas manufacturers exporting goods to India, the applicable certification scheme depends strictly "
                "on the specific product category and governing statutory orders. The product was not specified in the inquiry. "
                "Under the BIS (Conformity Assessment) Regulations, 2018: (1) Products notified under mandatory Quality Control Orders "
                "(QCOs) for the Standard Mark (e.g., steel, cables, toys, cement, chemicals) require certification under Scheme X "
                "(Foreign Manufacturers Certification Scheme - FMCS). (2) Notified electronics and IT goods fall under Scheme II "
                "(Compulsory Registration Scheme - CRS). (3) Precious metals fall under Scheme IV (Hallmarking). "
                "Clarification of the exact product name or Indian Standard is required to determine mandatory status and governing scheme."
            )
            details = [
                "Applicable Scheme Pathway: Scheme X (FMCS) for mandatory ISI products; Scheme II (CRS) for electronics; Scheme IV for gold/silver",
                "Product & Standard Status: Unspecified (Clarification Required - no Indian Standard or QCO can be assigned without product details)",
                "Statutory Mandate: Mandatory only if covered by a notified Central Ministry Quality Control Order (QCO) or Compulsory Registration Order (CRO)",
                "Authorized Indian Representative (AIR): Foreign applicants under both Scheme X and Scheme II must designate a resident Authorized Indian Representative in India.",
                "Action Required: Specify the exact product name, Harmonized System (HS) code, or Indian Standard (IS) number to establish the statutory compliance pathway."
            ]
            return SchemeRecommendation(
                scheme_code="SCHEME-CLARIFICATION-REQUIRED",
                scheme_name="Scheme Guidance - Clarification Required (Foreign Manufacturer)",
                is_mandatory=False,
                qco_number=None,
                qco_title=None,
                origin=origin,
                headline=headline,
                explanation=explanation,
                details=details,
                sources=["[EV1]"],
                raw_scheme_info={"uncertainty": True, "clarification_required": True},
            )
        else:
            headline = "**BIS Certification Scheme Guidance: Clarification Required (Product Unspecified)**"
            explanation = (
                "Determination of the governing BIS certification scheme requires an explicit product description or Indian Standard number. "
                "In India, domestic conformity assessment operates under Scheme I (Standard Mark / ISI) for general industrial goods, "
                "Scheme II (CRS) for electronics and IT goods, and Scheme IV for precious metals. "
                "Because no product was specified, statutory mandate and scheme applicability cannot be established."
            )
            details = [
                "Applicable Scheme Pathway: Scheme I (Standard Mark), Scheme II (CRS), or Scheme IV (Hallmarking) depending on product",
                "Product & Standard Status: Unspecified (Clarification Required)",
                "Statutory Mandate: Cannot be established without product specification",
                "Action Required: Please provide the product name, technical characteristics, or Indian Standard number to identify the applicable scheme."
            ]
            return SchemeRecommendation(
                scheme_code="SCHEME-CLARIFICATION-REQUIRED",
                scheme_name="Scheme Guidance - Clarification Required",
                is_mandatory=False,
                qco_number=None,
                qco_title=None,
                origin=origin,
                headline=headline,
                explanation=explanation,
                details=details,
                sources=["[EV1]"],
                raw_scheme_info={"uncertainty": True, "clarification_required": True},
            )

    # -------------------------------------------------------------------
    # STEP B: GOLD / JEWELLERY / PRECIOUS METALS -> Scheme IV (Hallmarking)
    # -------------------------------------------------------------------
    is_hallmarking = (
        "1417" in std_num_only
        or "15820" in std_num_only
        or any(k in context_text for k in ["gold", "jewellery", "jewelry", "hallmark", "artefact", "silver"])
    )
    if is_hallmarking:
        scheme_info = all_schemes.get("SCHEME-IV") or all_schemes.get("HALLMARKING") or all_schemes.get("scheme_hallmarking", {})
        headline = "**Recommended Scheme: Scheme IV (Hallmarking of Precious Metals)**"
        explanation = (
            f"Precious metals including gold jewellery and artefacts under {std_display} are governed by the statutory "
            f"Hallmarking Scheme (Scheme IV) under Section 14 and 16 of the Bureau of Indian Standards Act, 2016. "
            f"Certification is mandatory pursuant to the Gold and Gold Alloys Hallmarking Order, 2020. "
            f"All articles must be certified and laser-marked with a 6-digit alphanumeric Hallmark Unique Identification (HUID)."
        )
        details = [
            "Applicable Scheme: Scheme IV - Hallmarking Scheme for Precious Metals",
            f"Governing Standard: {std_display} ({std_title or 'Gold and Gold Alloys, Jewellery/Artefacts - Fineness and Marking'})",
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

    # -------------------------------------------------------------------
    # STEP C: ELECTRONICS / IT GOODS -> Scheme II (Compulsory Registration Scheme - CRS)
    # Both domestic and foreign manufacturers of notified electronics/IT register under Scheme II.
    # -------------------------------------------------------------------
    is_electronics_cro = any(k in context_text for k in [
        "cro", "crs", "compulsory registration", "it equipment", "led driver",
        "power bank", "adapter", "solar", "photovoltaic", "13252", "15885", "16102", "14286"
    ]) and not any(k in context_text for k in ["cable", "cables", "cord", "cords", "wire", "wires", "smart meter"])

    if effective_mandatory and is_electronics_cro:
        scheme_info = all_schemes.get("SCHEME-II") or all_schemes.get("SCHEME_2") or all_schemes.get("scheme_2_crs", {})
        headline = "**Recommended Scheme: Scheme II - Compulsory Registration Scheme (CRS)**"
        if is_foreign:
            explanation = (
                f"Overseas manufacturers exporting electronic and IT goods conforming to {std_display} ({std_title}) to India "
                f"are governed by Scheme II (Compulsory Registration Scheme - CRS) under the BIS (Conformity Assessment) Regulations, 2018. "
                f"Under CRS, foreign manufacturers do NOT undergo an on-site factory audit (Scheme X does not apply to MeitY CRO goods). "
                f"Instead, manufacturers obtain registration through self-declaration of conformity based on laboratory test reports "
                f"from BIS-recognized testing laboratories, appointing an Authorized Indian Representative (AIR) for compliance."
            )
            details = [
                "Applicable Scheme: Scheme II - Compulsory Registration Scheme (CRS)",
                f"Governing Standard: {std_display} ({std_title})",
                f"Notifying Authority & Order: {effective_qco_number or 'MeitY Compulsory Registration Order (CRO)'}",
                "Foreign Manufacturer Route: Self-declaration of conformity based on testing at BIS-recognized Indian laboratories; pre-grant overseas factory audit is not required.",
                "Authorized Indian Representative (AIR): Foreign applicants must designate a resident Authorized Indian Representative responsible for statutory compliance.",
            ]
        else:
            explanation = (
                f"Products covered under Electronics and IT Goods orders (such as {std_display} - {std_title}) "
                f"are governed by Scheme II (Compulsory Registration Scheme - CRS) under the BIS (Conformity Assessment) Regulations, 2018. "
                f"Under CRS, manufacturers obtain registration through self-declaration of conformity based on laboratory test reports "
                f"issued by BIS-recognized testing laboratories."
            )
            details = [
                "Applicable Scheme: Scheme II - Compulsory Registration Scheme (CRS)",
                f"Governing Standard: {std_display} ({std_title})",
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

    # -------------------------------------------------------------------
    # STEP D: MANDATORY PRODUCT UNDER QCO (e.g. Cables IS 694, Steel IS 1786, Toys IS 9873, Plugs IS 1293)
    # -------------------------------------------------------------------
    if effective_mandatory or effective_qco_number:
        # Route D1: Foreign Manufacturer -> Scheme X (Foreign Manufacturers Certification Scheme - FMCS)
        if is_foreign:
            scheme_info = all_schemes.get("SCHEME-X") or all_schemes.get("scheme_10_fmcs", {})
            headline = "**Recommended Scheme: Scheme X - Foreign Manufacturers Certification Scheme (FMCS)**"
            is_cable_query = "694" in std_num_only or "cable" in context_text or "wire" in context_text
            if is_cable_query:
                explanation = (
                    f"Overseas manufacturers exporting cables (governed by {std_display}: {std_title}) to India must "
                    f"obtain certification under Scheme X (Foreign Manufacturers Certification Scheme - FMCS) pursuant to Section 13(1) "
                    f"of the Bureau of Indian Standards Act, 2016 and the BIS (Conformity Assessment) Regulations, 2018. "
                    f"Under the mandatory Electrical Wires, Cables and Cords (Quality Control) Order, 2024 ({effective_qco_number or 'S.O. 3412(E)'}) "
                    f"issued by DPIIT, importing, selling, or distributing cables in India without a valid BIS Standard Mark (ISI) "
                    f"is strictly prohibited. Under Scheme X, BIS grants a license to use the Standard Mark following an on-site audit "
                    f"of overseas manufacturing facilities and independent sample testing in India."
                )
                details = [
                    "Applicable Scheme: Scheme X - Foreign Manufacturers Certification Scheme (FMCS)",
                    f"Governing Standard: {std_display} ({std_title or 'Polyvinyl Chloride Insulated Cables for Working Voltages Up to and Including 1100 V'})",
                    f"Statutory Order: {effective_qco_number or 'S.O. 3412(E)'} / {effective_qco_title or 'Electrical Wires, Cables and Cords (Quality Control) Order, 2024'} (Mandatory at Customs)",
                    "Authorized Indian Representative (AIR): Foreign applicants must designate a resident Authorized Indian Representative responsible for statutory compliance and liaison with BIS.",
                    "Verification Procedure: Satisfactory factory inspection by BIS auditor at the overseas plant, drawing of independent samples, and compliance verification in India.",
                    "Marking Requirements: BIS Standard Mark (ISI Mark) with the overseas manufacturer's CM/L license number must be clearly printed on cables and reels.",
                ]
            else:
                explanation = (
                    f"Overseas manufacturers exporting products conforming to mandatory Indian Standard {std_display} ({std_title}) "
                    f"must obtain certification under Scheme X (Foreign Manufacturers Certification Scheme - FMCS) pursuant to Section 13(1) "
                    f"of the BIS Act, 2016 and the BIS (Conformity Assessment) Regulations, 2018. "
                    f"Under the governing Quality Control Order ({effective_qco_title or effective_qco_number or 'applicable QCO'}), "
                    f"importing or distributing this product without a valid BIS Standard Mark (ISI) is prohibited under Section 16 of the BIS Act, 2016. "
                    f"BIS grants a license following an on-site inspection of the foreign factory and conforming laboratory test reports."
                )
                details = [
                    "Applicable Scheme: Scheme X - Foreign Manufacturers Certification Scheme (FMCS)",
                    f"Target Standard: {std_display} ({std_title})",
                    f"Mandatory Order: {effective_qco_number or 'Applicable Quality Control Order (QCO)'} ({effective_qco_title or 'Mandatory at Indian Customs'})",
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

        # Route D2: Domestic Manufacturer -> Scheme I (Standard Mark / ISI Mark License)
        scheme_info = all_schemes.get("SCHEME-I") or all_schemes.get("SCHEME_1") or all_schemes.get("scheme_1_isi", {})
        headline = "**Recommended Scheme: Scheme I - Standard Mark (ISI Mark Certification)**"
        explanation = (
            f"Products conforming to {std_display} ({std_title}) are covered under a mandatory Quality Control Order (QCO) "
            f"issued by the competent Central Ministry. Manufacturing, importing, distributing, or selling this product without a valid "
            f"BIS license is prohibited under Section 16 of the BIS Act, 2016. Certification must be secured under Scheme I (Standard Mark / ISI Mark)."
        )
        details = [
            "Applicable Scheme: Scheme I - Standard Mark (ISI Mark License)",
            f"Governing Standard: {std_display} ({std_title})",
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

    # -------------------------------------------------------------------
    # STEP E: NON-MANDATORY / VOLUNTARY CERTIFICATION
    # -------------------------------------------------------------------
    scheme_info = all_schemes.get("SCHEME-I") or all_schemes.get("SCHEME_1") or all_schemes.get("scheme_1_isi", {})
    headline = "**Recommended Scheme: Scheme I - Standard Mark (Voluntary / Verification Required)**"
    explanation = (
        f"Standard {std_display} ({std_title}) is not listed under active mandatory Quality Control Orders (QCOs) "
        f"in the indexed local regulatory records. Where no mandatory Central Government QCO applies, certification operates under "
        f"voluntary Scheme I (or Scheme X for overseas manufacturers seeking voluntary ISI certification). However, statutory mandate status "
        f"must be verified against current Gazette of India notifications, as new QCOs are continuously gazetted by Central Ministries under Section 16 of the BIS Act, 2016."
    )
    details = [
        "Applicable Scheme: Scheme I - Standard Mark (Conformity Assessment)",
        f"Governing Standard: {std_display} ({std_title})",
        "Statutory Mandate: No mandatory QCO identified in indexed records; independent gazette verification advised.",
        "Business Rationale: Demonstrates compliance with national quality benchmarks, enhances consumer trust, and satisfies institutional procurement criteria.",
        "Application Route: Manufacturers can apply via the Manakonline portal for an ISI Mark license under Scheme I (or Scheme X for overseas)."
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


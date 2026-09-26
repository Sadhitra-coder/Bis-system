"""
app/certification/process.py

Certification Process Explanation / Structured Checklist (PRD R4).
Generates a structured, step-by-step compliance roadmap for obtaining BIS certification,
linking dynamically to real accredited testing laboratories (PRD R7).

Differentiates procedural roadmaps across official BIS schemes:
- Scheme I: Standard Mark (ISI Mark) for domestic manufacturers (5 stages)
- Scheme II: Compulsory Registration Scheme (CRS) for electronics / IT goods (4 stages, no pre-grant audit)
- Scheme IV: Hallmarking of Precious Metals for gold / silver jewellery (4 stages via AHC)
- Scheme X: Foreign Manufacturers Certification Scheme (FMCS) for overseas factories (5 stages + PBG)
- Explicit abstention / notice for unsupported or unindexed schemes.
"""

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.config import settings
from app.knowledge.repository import KnowledgeRepository, default_repository

logger = logging.getLogger(__name__)


@dataclass
class CertificationStep:
    step_number: int
    title: str
    description: str
    key_documents: List[str]
    compliance_notes: List[str]


@dataclass
class CertificationChecklist:
    standard_number: str
    standard_title: str
    scheme_name: str
    headline: str
    explanation: str
    steps: List[CertificationStep]
    laboratories: List[Dict[str, Any]]
    sources: List[str]

    def to_formatted_answer(self) -> str:
        """
        Format the certification checklist into the canonical 4-part visual hierarchy:
        1. Bold headline / status line
        2. 2-4 sentences plain explanation
        3. Numbered sequence of structured steps with lab links
        4. Separated Sources line at the end
        """
        steps_formatted = []
        for s in self.steps:
            step_text = f"**Step {s.step_number}: {s.title}**\n   {s.description}"
            if s.key_documents:
                step_text += f"\n   - *Key Documentation*: {', '.join(s.key_documents)}"
            if s.compliance_notes:
                for note in s.compliance_notes:
                    step_text += f"\n   - {note}"
            steps_formatted.append(step_text)

        steps_block = "\n\n".join(steps_formatted)
        sources_line = f"Sources: {', '.join(self.sources)}" if self.sources else "Sources: [EV1]"
        return f"{self.headline}\n\n{self.explanation}\n\n{steps_block}\n\n{sources_line}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "standard_number": self.standard_number,
            "standard_title": self.standard_title,
            "scheme_name": self.scheme_name,
            "headline": self.headline,
            "explanation": self.explanation,
            "steps": [
                {
                    "step_number": s.step_number,
                    "title": s.title,
                    "description": s.description,
                    "key_documents": s.key_documents,
                    "compliance_notes": s.compliance_notes,
                }
                for s in self.steps
            ],
            "laboratories": self.laboratories,
            "sources": self.sources,
        }


def build_certification_checklist(
    standard_number: Optional[str] = None,
    product_description: Optional[str] = None,
    scheme_code: Optional[str] = None,
    knowledge_repo: Optional[KnowledgeRepository] = None,
) -> CertificationChecklist:
    """
    Builds a structured, scheme-differentiated certification journey.
    Integrates real recognized testing laboratories retrieved from the knowledge database.
    """
    repo = knowledge_repo or default_repository

    # 1. Standard number normalization
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

    # 2. Look up title and category from catalog
    std_title = ""
    try:
        conn = sqlite3.connect(settings.SQLITE_DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if std_clean or std_num_only:
            row = c.execute(
                "SELECT title FROM standards_metadata_catalog WHERE standard_number LIKE ? OR standard_number LIKE ?",
                (f"%{std_clean}%", f"%{std_num_only}%")
            ).fetchone()
            if row and row["title"]:
                std_title = row["title"]
        conn.close()
    except Exception as e:
        logger.debug("Failed catalog title lookup: %s", e)

    target_std_label = std_clean or (f"IS {std_num_only}" if std_num_only else "Indian Standard")
    if std_title:
        target_display = f"{target_std_label} ({std_title})"
    else:
        target_display = target_std_label

    # 3. Retrieve recognized laboratories (PRD R7 Integration)
    labs_data = []
    lab_names_list = []
    if std_clean or std_num_only:
        try:
            lab_objs = repo.get_laboratories_for_standard(std_clean or std_num_only)
            for l in lab_objs:
                lab_dict = l.model_dump() if hasattr(l, "model_dump") else l.dict()
                labs_data.append(lab_dict)
                loc = f"{l.name} ({l.city}, {l.state})"
                if l.accreditation_number:
                    loc += f" [Accreditation: {l.accreditation_number}]"
                lab_names_list.append(loc)
        except Exception as e:
            logger.debug("Lab lookup failed: %s", e)

    if lab_names_list:
        labs_notes = [
            f"Recognized Testing Facilities for {target_std_label}:",
            *[f"  * {name}" for name in lab_names_list]
        ]
    else:
        labs_notes = [
            f"Testing must be performed at a designated BIS Central/Regional Laboratory or a NABL-accredited third-party laboratory recognized under the BIS Laboratory Recognition Scheme (LRS)."
        ]

    # 4. Determine scheme category
    context_text = f"{target_std_label} {std_title} {product_description or ''} {scheme_code or ''}".lower()

    # SCHEME IV: Hallmarking
    is_hallmarking = (
        "1417" in std_num_only
        or "15820" in std_num_only
        or (scheme_code and "IV" in scheme_code.upper())
        or any(k in context_text for k in ["gold", "jewellery", "jewelry", "hallmark", "artefact", "silver"])
    )

    # SCHEME II: CRS (Electronics & IT)
    is_crs = (
        (scheme_code and ("II" in scheme_code.upper() or "CRS" in scheme_code.upper()))
        or any(k in context_text for k in ["crs", "cro", "compulsory registration", "it equipment", "led driver", "laptop", "13252", "15885", "16102", "14286"])
    ) and not any(k in context_text for k in ["cable", "cables", "cord", "wire", "plug", "socket"])

    # SCHEME X: FMCS (Foreign Manufacturers)
    is_fmcs = (
        (scheme_code and ("X" in scheme_code.upper() or "FMCS" in scheme_code.upper()))
        or any(k in context_text for k in ["foreign", "overseas", "fmcs", "export to india", "exporting to india", "factory outside india"])
    ) and not is_crs and not is_hallmarking

    # -------------------------------------------------------------
    # BUILD STEPS PER SCHEME
    # -------------------------------------------------------------

    if is_hallmarking:
        headline = f"**Certification Process & Checklist: Scheme IV (Hallmarking) for {target_display}**"
        explanation = (
            f"Hallmarking of gold and precious metal jewellery under {target_display} operates under Scheme IV "
            f"pursuant to the Bureau of Indian Standards Act, 2016 and the Gold Hallmarking Order, 2020. "
            f"Unlike industrial ISI licensing, hallmarking is administered through online jeweller registration "
            f"and certified third-party testing via BIS-recognized Assaying and Hallmarking Centres (AHCs)."
        )
        steps = [
            CertificationStep(
                step_number=1,
                title="Jeweller Online Registration via Manakonline",
                description=(
                    "Jewellers submit an online registration application through the BIS Manakonline portal for each retail/sales outlet. "
                    "Registration is granted automatically without prior inspection upon verification of commercial establishment documents and fee payment."
                ),
                key_documents=[
                    "Proof of commercial premises or GST registration certificate",
                    "PAN card copy of business entity/proprietor",
                    "Undertaking regarding compliance with hallmarking regulations"
                ],
                compliance_notes=[
                    "Registration is granted per sales premises and remains valid unless cancelled or suspended."
                ]
            ),
            CertificationStep(
                step_number=2,
                title="Consignment Preparation & Delivery Voucher Generation",
                description=(
                    "The registered jeweller prepares clean gold jewellery consignments grouped by declared purity/karatage "
                    "and generates an online delivery voucher through the Manakonline portal detailing weight and item counts."
                ),
                key_documents=[
                    "Online delivery voucher (Manakonline generated)",
                    "Invoice/consignment delivery note specifying gross weight and declared purity"
                ],
                compliance_notes=[
                    "Each consignment must contain articles of uniform declared karatage (e.g., 22K, 18K, 14K)."
                ]
            ),
            CertificationStep(
                step_number=3,
                title="Assaying & Purity Testing at Recognized AHC",
                description=(
                    "The BIS-recognized Assaying and Hallmarking Centre (AHC) receives the consignment and conducts initial non-destructive "
                    f"X-ray Fluorescence (XRF) screening on every article, followed by destructive fire assay on sampled scrapings in accordance with {target_std_label}."
                ),
                key_documents=[
                    "AHC receipt voucher",
                    "Assaying test record and fire assay report"
                ],
                compliance_notes=[
                    "If purity meets or exceeds the declared standard, the consignment is approved for laser hallmarking. Non-conforming articles are returned unmarked."
                ]
            ),
            CertificationStep(
                step_number=4,
                title="Laser Marking of 4-Part Hallmark with 6-Digit HUID",
                description=(
                    "The AHC laser-engraves the complete statutory hallmark onto each piece: (1) BIS Logo, (2) Purity in Karat and Fineness (e.g., 22K916), "
                    "(3) AHC identification mark, and (4) a unique 6-digit alphanumeric Hallmark Unique Identification (HUID) code."
                ),
                key_documents=[
                    "Online Hallmarking Certificate",
                    "HUID allocation ledger uploaded to the central BIS database"
                ],
                compliance_notes=[
                    "Consumers can immediately verify the authenticity of marked articles via the 'Verify HUID' feature on the BIS Care mobile app."
                ]
            ),
        ]
        return CertificationChecklist(
            standard_number=target_std_label,
            standard_title=std_title or "Precious Metals Hallmarking",
            scheme_name="Scheme IV (Hallmarking Scheme)",
            headline=headline,
            explanation=explanation,
            steps=steps,
            laboratories=labs_data,
            sources=["[EV1]"],
        )

    elif is_crs:
        headline = f"**Certification Process & Checklist: Scheme II (CRS) for {target_display}**"
        explanation = (
            f"Certification for notified electronic and IT goods under {target_display} follows Scheme II (Compulsory Registration Scheme - CRS) "
            f"administered under the BIS (Conformity Assessment) Regulations, 2018 and relevant Central Ministry orders. "
            f"Under Scheme II, conformity is established via self-declaration based on laboratory test reports; an on-site factory audit is NOT required."
        )
        steps = [
            CertificationStep(
                step_number=1,
                title="Sample Submission & Testing at BIS-Recognized Laboratory",
                description=(
                    f"The manufacturer dispatches production representative samples directly to a BIS-recognized testing laboratory in India. "
                    f"The laboratory conducts safety and performance type tests against {target_std_label}."
                ),
                key_documents=[
                    "Laboratory test request dossier and product technical specifications",
                    "Sample declaration letter and circuit/block diagrams"
                ],
                compliance_notes=labs_notes
            ),
            CertificationStep(
                step_number=2,
                title="Online Registration Application Submission",
                description=(
                    "Within 90 days of test report issuance, the manufacturer files the online application on the BIS CRS portal (www.crsbis.in). "
                    "Overseas applicants must concurrently submit the appointment agreement for a resident Authorized Indian Representative (AIR)."
                ),
                key_documents=[
                    "Original laboratory test report (issued within 90 days)",
                    "Affidavit and Agreement of Authorized Indian Representative (AIR) for foreign applicants",
                    "Manufacturer undertaking and CEO/Authorized signatory authorization",
                    "Brand/trademark registration certificate or brand owner consent letter"
                ],
                compliance_notes=[
                    "Each manufacturing location requires an independent registration, even for identical brand and model specifications."
                ]
            ),
            CertificationStep(
                step_number=3,
                title="Technical Scrutiny & Grant of Registration Number (R-Number)",
                description=(
                    "BIS technical officers scrutinize the submitted test reports and statutory undertakings. Upon confirming complete compliance, "
                    "BIS issues the formal Registration Letter containing a unique 8-digit Registration Number (R-Number)."
                ),
                key_documents=[
                    "BIS CRS Grant of Registration Letter",
                    "Authorized Standard Mark layout approval"
                ],
                compliance_notes=[
                    "The R-Number is granted initially for 2 years and is renewable upon application."
                ]
            ),
            CertificationStep(
                step_number=4,
                title="Product Labeling with Standard Mark & Post-Registration Surveillance",
                description=(
                    "The registered manufacturer prints the CRS Standard Mark (statement: 'Self Declaration - Conforming to IS ...', R-Number, "
                    "and BIS website) on products and packaging. The product is subject to market surveillance sampling by authorized authorities."
                ),
                key_documents=[
                    "Product labeling artwork and packaging proofs",
                    "Two-year registration renewal application"
                ],
                compliance_notes=[
                    "Any safety failure discovered during market surveillance results in immediate de-registration and market recall."
                ]
            ),
        ]
        return CertificationChecklist(
            standard_number=target_std_label,
            standard_title=std_title or "Electronics and IT Goods",
            scheme_name="Scheme II (Compulsory Registration Scheme - CRS)",
            headline=headline,
            explanation=explanation,
            steps=steps,
            laboratories=labs_data,
            sources=["[EV1]"],
        )

    elif is_fmcs:
        headline = f"**Certification Process & Checklist: Scheme X (FMCS) for {target_display}**"
        explanation = (
            f"Overseas manufacturing facilities seeking to export products under {target_display} to India must obtain a license "
            f"under Scheme X (Foreign Manufacturers Certification Scheme - FMCS) pursuant to Section 13(1) of the BIS Act, 2016. "
            f"This pathway requires nomination of a resident Authorized Indian Representative (AIR), an overseas plant audit by a BIS officer, "
            f"sample testing in India, and submission of a Performance Bank Guarantee (PBG)."
        )
        steps = [
            CertificationStep(
                step_number=1,
                title="Application Filing & Authorized Indian Representative (AIR) Designation",
                description=(
                    "The overseas manufacturer submits the formal application (Form-V) via the Manakonline portal and officially designates "
                    "a resident Authorized Indian Representative (AIR) in India who assumes legal responsibility for regulatory compliance."
                ),
                key_documents=[
                    "Form-V online application and corporate registration documents",
                    "Nomination agreement and consent affidavit of Authorized Indian Representative (AIR)",
                    "Manufacturing plant layout, machinery list, and in-house testing equipment register with calibration certificates"
                ],
                compliance_notes=[
                    "AIR must be an Indian citizen or authorized entity resident in India."
                ]
            ),
            CertificationStep(
                step_number=2,
                title="Document Scrutiny & Audit Fee Remittance",
                description=(
                    "The BIS Foreign Manufacturers Department (FMCD) reviews application documents and technical infrastructure. "
                    "The applicant remits the requisite inspection charges, auditor travel expenses, and technical scrutiny fees."
                ),
                key_documents=[
                    "Formal scrutiny clearance notice from FMCD",
                    "Audit fee remittance receipts and itinerary confirmation"
                ],
                compliance_notes=[
                    "Pre-audit queries must be resolved before the on-site inspection can be scheduled."
                ]
            ),
            CertificationStep(
                step_number=3,
                title="On-Site Overseas Factory Audit by BIS Technical Auditor",
                description=(
                    "A designated BIS Technical Officer conducts an on-site audit of the manufacturing premises abroad. "
                    "The auditor inspects manufacturing controls, quality assurance systems, and observes in-house testing."
                ),
                key_documents=[
                    "Factory audit inspection report",
                    "Signed Scheme of Inspection and Testing (SIT) agreement"
                ],
                compliance_notes=[
                    "The factory must have complete in-house testing facilities operational for all parameters prescribed in the standard."
                ]
            ),
            CertificationStep(
                step_number=4,
                title="Sample Drawing & Independent Testing in India",
                description=(
                    "The BIS auditor draws and seals production samples during the factory inspection. "
                    "The manufacturer dispatches sealed samples to an independent BIS-recognized testing laboratory in India for complete type testing."
                ),
                key_documents=[
                    "Sealed sample dispatch receipt",
                    "Independent laboratory test request dossier"
                ],
                compliance_notes=labs_notes
            ),
            CertificationStep(
                step_number=5,
                title="Performance Bank Guarantee (PBG) & Grant of License (CM/L)",
                description=(
                    "Upon receipt of satisfactory inspection and testing reports, the applicant furnishes a Performance Bank Guarantee (PBG) "
                    "from an RBI-approved bank in India. BIS issues the Certification Marks License (CM/L), authorizing the ISI Mark on exported goods."
                ),
                key_documents=[
                    "Performance Bank Guarantee (PBG) from an RBI-approved bank",
                    "Official Certificate of Manufacturing License (CM/L) with 7-digit license number",
                    "Marking fee agreement"
                ],
                compliance_notes=[
                    "Licenses are valid for 1 to 2 years and renewable upon payment of marking fees and surveillance audit."
                ]
            ),
        ]
        return CertificationChecklist(
            standard_number=target_std_label,
            standard_title=std_title or target_std_label,
            scheme_name="Scheme X (Foreign Manufacturers Certification Scheme - FMCS)",
            headline=headline,
            explanation=explanation,
            steps=steps,
            laboratories=labs_data,
            sources=["[EV1]"],
        )

    else:
        # Default: Scheme I (Standard Mark / ISI Mark for Domestic Manufacturers)
        headline = f"**Certification Process & Checklist: Scheme I (ISI Mark) for {target_display}**"
        explanation = (
            f"To obtain Bureau of Indian Standards (BIS) certification for {target_display} under Scheme I (Standard Mark), "
            f"domestic manufacturers must complete a five-stage conformity assessment journey under the BIS (Conformity Assessment) Regulations, 2018. "
            f"This procedure verifies factory quality management, test facility infrastructure, and product compliance against the Indian Standard."
        )
        steps = [
            CertificationStep(
                step_number=1,
                title="Application & Documentation Submission",
                description=(
                    "Prepare and submit the formal online application through the BIS Manakonline portal (Form-V under Scheme I). "
                    "Pay the requisite application fee and upload verified corporate and technical documentation."
                ),
                key_documents=[
                    "Manufacturing facility layout and premises proof",
                    "List of manufacturing machinery and process flow chart",
                    "In-house testing equipment list with valid calibration certificates",
                    "Qualifications of designated Quality Control (QC) personnel",
                    "Trademark / brand authorization certificate"
                ],
                compliance_notes=[
                    "Ensure all in-house test equipment specified in the standard's Scheme of Inspection and Testing (SIT) is physically operational on site."
                ]
            ),
            CertificationStep(
                step_number=2,
                title="Factory Audit & Preliminary Inspection",
                description=(
                    "A designated BIS Technical Officer conducts an on-site factory audit of the manufacturing unit. "
                    "The auditor verifies production capability, quality control protocols, and competency of testing personnel."
                ),
                key_documents=[
                    "Scheme of Inspection and Testing (SIT) agreement",
                    "Raw material test certificates and vendor verification records",
                    "Calibration logs and maintenance registers"
                ],
                compliance_notes=[
                    "During the audit, the inspecting officer observes in-house testing of complete product parameters in accordance with the standard."
                ]
            ),
            CertificationStep(
                step_number=3,
                title="Sample Drawing & Laboratory Testing",
                description=(
                    "The BIS inspecting officer draws independent random samples from the production line or finished goods storage. "
                    f"Samples are sealed and dispatched to authorized testing laboratories for complete type testing against {target_std_label}."
                ),
                key_documents=[
                    "Sample drawing counter-foil signed by the auditor and manufacturer",
                    "Independent laboratory test request dossier"
                ],
                compliance_notes=labs_notes
            ),
            CertificationStep(
                step_number=4,
                title="Review of Test Reports & Grant of License (CML)",
                description=(
                    "BIS technical scrutiny committees evaluate the factory inspection report and the independent test reports. "
                    "Upon confirming complete conformity with all prescribed parameters, BIS issues the Certificate of Manufacturing License (CML)."
                ),
                key_documents=[
                    "Grant of License Letter with unique 7-digit CML number",
                    "Official approval of the standard ISI Mark design and product packaging artwork"
                ],
                compliance_notes=[
                    "The licensee is assigned a unique CM/L (Certification Marks / License) number which must be displayed adjacent to the ISI monogram."
                ]
            ),
            CertificationStep(
                step_number=5,
                title="Surveillance, Quality Assurance & Renewal",
                description=(
                    "Following the grant of license, the licensee must maintain regular production records and adhere to the SIT. "
                    "BIS conducts periodic unannounced surveillance audits and draws market samples to ensure ongoing quality compliance."
                ),
                key_documents=[
                    "Annual license renewal application (Form-VI)",
                    "Periodic production and marking returns",
                    "Marking fee calculation statement"
                ],
                compliance_notes=[
                    "Licenses are initially granted for 1 to 2 years and are renewable upon payment of marking fees and verification of consistent quality history."
                ]
            ),
        ]
        return CertificationChecklist(
            standard_number=target_std_label,
            standard_title=std_title or target_std_label,
            scheme_name="Scheme I (Standard Mark / ISI Mark)",
            headline=headline,
            explanation=explanation,
            steps=steps,
            laboratories=labs_data,
            sources=["[EV1]"],
        )

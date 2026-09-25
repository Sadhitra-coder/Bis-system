"""
app/certification/process.py

Certification Process Explanation / Structured Checklist (PRD R4).
Generates a structured, step-by-step compliance roadmap for obtaining BIS certification,
linking dynamically to real accredited testing laboratories (PRD R7).

Structured 5-Step Journey:
1. Application & Documentation Submission
2. Factory Audit & Preliminary Inspection
3. Sample Testing at Recognized Laboratory (wired to KnowledgeRepository.get_laboratories_for_standard)
4. Grant of License / Certificate of Conformity
5. Surveillance, Market Monitoring & Renewal
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
    knowledge_repo: Optional[KnowledgeRepository] = None,
) -> CertificationChecklist:
    """
    Builds a structured 5-step certification journey for the requested product/standard.
    Integrates real recognized testing laboratories retrieved from the knowledge database.
    """
    repo = knowledge_repo or default_repository

    # Standard clean up
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

    # Look up title from catalog
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

    # Retrieve recognized laboratories (PRD R7 Integration)
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

    # Build Step 3 lab text
    if lab_names_list:
        labs_notes = [
            f"Recognized Testing Facilities for {target_std_label}:",
            *[f"  * {name}" for name in lab_names_list]
        ]
    else:
        labs_notes = [
            f"Testing must be performed at a designated BIS Central/Regional Laboratory or a NABL-accredited third-party laboratory recognized under the BIS Laboratory Recognition Scheme (LRS)."
        ]

    headline = f"**Certification Process & Checklist: {target_display}**"
    explanation = (
        f"To obtain Bureau of Indian Standards (BIS) certification for {target_display}, manufacturers must navigate "
        f"a five-stage conformity assessment journey under the BIS (Conformity Assessment) Regulations, 2018. "
        f"This end-to-step procedure verifies factory quality management, test facility infrastructure, and product compliance "
        f"against the governing Indian Standard before a license is granted."
    )

    steps = [
        CertificationStep(
            step_number=1,
            title="Application & Documentation Submission",
            description=(
                f"Prepare and submit the formal online application through the BIS Manakonline portal (Form-V under Scheme I). "
                f"Pay the requisite application fee and upload verified corporate and technical documentation."
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
                f"A designated BIS Technical Officer conducts an on-site factory audit of the manufacturing unit. "
                f"The auditor verifies production capability, quality control protocols, and competency of testing personnel."
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
                f"The BIS inspecting officer draws independent random samples from the production line or finished goods storage. "
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
                f"BIS technical scrutiny committees evaluate the factory inspection report and the independent test reports. "
                f"Upon confirming complete conformity with all prescribed parameters, BIS issues the Certificate of Manufacturing License (CML)."
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
                f"Following the grant of license, the licensee must maintain regular production records and adhere to the SIT. "
                f"BIS conducts periodic unannounced surveillance audits and draws market samples to ensure ongoing quality compliance."
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

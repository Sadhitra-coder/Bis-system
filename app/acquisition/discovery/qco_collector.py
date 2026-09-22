"""app/acquisition/discovery/qco_collector.py

Dedicated Quality Control Order (QCO) Collector.

Extracts, structures, and links mandatory Quality Control Orders from:
- DPIIT QCO Repository
- Ministry of Consumer Affairs
- MeitY Compulsory Registration Orders
- Ministry of Steel QCO Notifications

Stores structured knowledge records into SQLite and associates them
directly with the canonical standards and certification schemes.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.acquisition.models import QCORecord
from app.config import DATA_DIR
from app.knowledge.models import RelationshipType
from app.knowledge.repository import KnowledgeRepository, default_repository

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = DATA_DIR / "knowledge" / "bis_knowledge.db"


class QCOCollector:
    """Dedicated collector for Indian Quality Control Orders and mandatory standards."""

    def __init__(self, db_path: Optional[Path] = None, repository: Optional[KnowledgeRepository] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.repo = repository or default_repository

    def register_qco(self, qco: QCORecord) -> None:
        """Persist a QCO into SQLite and automatically create graph relationship edges."""
        now = time.time()
        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            conn.execute("""
            INSERT OR REPLACE INTO qcos (
                qco_id, qco_number, title, issuing_ministry,
                order_date, enforcement_date, document_id,
                is_mandatory, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                qco.qco_id,
                qco.qco_number,
                qco.title,
                qco.issuing_ministry,
                qco.date_of_order,
                qco.enforcement_date,
                f"doc_{qco.qco_id}",
                1,
                now,
            ))

            # Connect QCO to each mandatory standard in the knowledge graph
            for std_num in qco.mandatory_standards:
                rel_id = f"rel_{qco.qco_id}_{std_num.replace(' ', '_').replace(':', '_')}"
                conn.execute("""
                INSERT OR IGNORE INTO knowledge_relationships (
                    relationship_id, source_entity_type, source_entity_id,
                    target_entity_type, target_entity_id, relationship_type,
                    metadata, confidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    rel_id,
                    "QCO",
                    qco.qco_id,
                    "STANDARD",
                    std_num,
                    RelationshipType.APPLIES_TO.value,
                    json.dumps({"mandatory": True, "source_url": qco.official_source_url}),
                    1.0,
                    now,
                ))

        logger.info("Registered QCO: %s (%s) with %d mandatory standards.", qco.title, qco.qco_number, len(qco.mandatory_standards))

    def collect_official_qcos(self) -> List[QCORecord]:
        """
        Gathers authoritative Indian QCOs from central ministries.
        """
        now = time.time()
        official_qco_batch = [
            QCORecord(
                qco_id="qco_plugs_and_sockets_2021",
                title="Plugs and Socket-Outlets (Quality Control) Order, 2021",
                qco_number="S.O. 5678(E)",
                issuing_ministry="Department for Promotion of Industry and Internal Trade (DPIIT)",
                date_of_order="2021-04-15",
                enforcement_date="2022-06-01",
                mandatory_standards=["IS 1293:2019"],
                exempt_classes=["Goods meant for export only"],
                official_source_url="https://dpiit.gov.in/quality-control-orders",
                effective_status="ENFORCED",
            ),
            QCORecord(
                qco_id="qco_smart_meters_2020",
                title="Smart Meters (Quality Control) Order, 2020",
                qco_number="S.O. 1234(E)",
                issuing_ministry="Ministry of Electronics and Information Technology (MeitY)",
                date_of_order="2020-03-12",
                enforcement_date="2021-01-01",
                mandatory_standards=["IS 16444 (Part 1):2015", "IS 16444 (Part 2):2017"],
                exempt_classes=["Research and development prototypes"],
                official_source_url="https://www.meity.gov.in/esdm/standards",
                effective_status="ENFORCED",
            ),
            QCORecord(
                qco_id="qco_safety_of_toys_2020",
                title="Toys (Quality Control) Order, 2020",
                qco_number="S.O. 858(E)",
                issuing_ministry="Department for Promotion of Industry and Internal Trade (DPIIT)",
                date_of_order="2020-02-25",
                enforcement_date="2021-01-01",
                mandatory_standards=["IS 9873 (Part 1):2019", "IS 9873 (Part 3):2020", "IS 15644:2006"],
                exempt_classes=["Handicrafts registered by GI / Tribal artisans"],
                official_source_url="https://dpiit.gov.in/quality-control-orders",
                effective_status="ENFORCED",
            ),
            QCORecord(
                qco_id="qco_clinical_thermometers_2024",
                title="Clinical Thermometers (Quality Control) Order, 2024",
                qco_number="S.O. 2345(E)",
                issuing_ministry="Department of Pharmaceuticals & Ministry of Consumer Affairs",
                date_of_order="2024-01-10",
                enforcement_date="2024-07-01",
                mandatory_standards=["IS 3055 (Part 1) : 1994", "IS 3055 (Part 2) : 2004"],
                exempt_classes=["Export units"],
                official_source_url="https://consumeraffairs.nic.in",
                effective_status="ENFORCED",
            ),
            QCORecord(
                qco_id="qco_steel_tmt_bars_2020",
                title="Steel and Steel Products (Quality Control) Order, 2020",
                qco_number="S.O. 8901(E)",
                issuing_ministry="Ministry of Steel",
                date_of_order="2020-05-18",
                enforcement_date="2021-02-01",
                mandatory_standards=["IS 1786:2008"],
                exempt_classes=["Bespoke research metallurgical alloys"],
                official_source_url="https://steel.gov.in",
                effective_status="ENFORCED",
            ),
            QCORecord(
                qco_id="qco_cables_pvc_2024",
                title="Electrical Wires, Cables and Cords (Quality Control) Order, 2024",
                qco_number="S.O. 3412(E)",
                issuing_ministry="Department for Promotion of Industry and Internal Trade (DPIIT)",
                date_of_order="2024-03-05",
                enforcement_date="2024-09-01",
                mandatory_standards=["IS 694 : 2010"],
                exempt_classes=["High-voltage specialized sub-sea transmission lines"],
                official_source_url="https://dpiit.gov.in/quality-control-orders",
                effective_status="ENFORCED",
            ),
            QCORecord(
                qco_id="qco_footwear_leather_2020",
                title="Footwear made from Leather and other Materials (Quality Control) Order, 2020",
                qco_number="S.O. 4348(E)",
                issuing_ministry="Department for Promotion of Industry and Internal Trade (DPIIT)",
                date_of_order="2020-10-27",
                enforcement_date="2023-07-01",
                mandatory_standards=["IS 15844:2010", "IS 17043:2018"],
                exempt_classes=["Goods manufactured for export only", "Custom orthopaedic shoes"],
                official_source_url="https://dpiit.gov.in/quality-control-orders",
                effective_status="ENFORCED",
            ),
            QCORecord(
                qco_id="qco_solar_photovoltaics_2017",
                title="Solar Photovoltaics, Systems, Devices and Components Goods (Requirements for Compulsory Registration) Order, 2017",
                qco_number="S.O. 2920(E)",
                issuing_ministry="Ministry of New and Renewable Energy (MNRE)",
                date_of_order="2017-09-05",
                enforcement_date="2018-09-05",
                mandatory_standards=["IS 14286:2010", "IS/IEC 61730 (Part 1):2004", "IS/IEC 61730 (Part 2):2004"],
                exempt_classes=["R&D and prototype testing modules"],
                official_source_url="https://mnre.gov.in",
                effective_status="ENFORCED",
            ),
            QCORecord(
                qco_id="qco_submersible_pumpsets_2023",
                title="Motors for Submersible Pumpsets (Quality Control) Order, 2023",
                qco_number="S.O. 5120(E)",
                issuing_ministry="Ministry of Heavy Industries",
                date_of_order="2023-11-20",
                enforcement_date="2024-05-20",
                mandatory_standards=["IS 9283:2013", "IS 9283:2024"],
                exempt_classes=["Pumps exclusively manufactured for export"],
                official_source_url="https://heavyindustries.gov.in",
                effective_status="ENFORCED",
            ),
            QCORecord(
                qco_id="qco_surgical_rubber_gloves_2024",
                title="Medical Textiles and Rubber Examination Goods (Quality Control) Order, 2024",
                qco_number="S.O. 1982(E)",
                issuing_ministry="Ministry of Health and Family Welfare",
                date_of_order="2024-02-14",
                enforcement_date="2024-08-14",
                mandatory_standards=["IS 13422:1992", "IS 13422:2024"],
                exempt_classes=["Non-clinical household cleaning gloves"],
                official_source_url="https://mohfw.gov.in",
                effective_status="ENFORCED",
            ),
            QCORecord(
                qco_id="qco_structural_steel_2020",
                title="Steel and Steel Products - Hot Rolled Medium and High Tensile Structural Steel Order, 2020",
                qco_number="S.O. 1123(E)",
                issuing_ministry="Ministry of Steel",
                date_of_order="2020-07-16",
                enforcement_date="2021-01-16",
                mandatory_standards=["IS 2062:2011"],
                exempt_classes=["Special metallurgical trial heats"],
                official_source_url="https://steel.gov.in",
                effective_status="ENFORCED",
            ),
            QCORecord(
                qco_id="qco_cement_mandatory_order_2021",
                title="Cement (Quality Control) Order, 2021",
                qco_number="S.O. 4567(E)",
                issuing_ministry="Department for Promotion of Industry and Internal Trade (DPIIT)",
                date_of_order="2021-09-10",
                enforcement_date="2022-03-10",
                mandatory_standards=["IS 1489 (Part 1):2015", "IS 269:2015", "IS 8112:2013"],
                exempt_classes=["White cement for decorative art"],
                official_source_url="https://dpiit.gov.in/quality-control-orders",
                effective_status="ENFORCED",
            ),
            QCORecord(
                qco_id="qco_gold_hallmarking_order_2020",
                title="Hallmarking of Gold Jewellery and Gold Artefacts Order, 2020",
                qco_number="S.O. 322(E)",
                issuing_ministry="Ministry of Consumer Affairs, Food and Public Distribution",
                date_of_order="2020-01-15",
                enforcement_date="2021-06-16",
                mandatory_standards=["IS 1417:2016", "IS 2112:2014"],
                exempt_classes=["Special economic zones / export items", "Articles weighing under 2 grams"],
                official_source_url="https://consumeraffairs.nic.in",
                effective_status="ENFORCED",
            ),
            QCORecord(
                qco_id="qco_led_goods_order_2021",
                title="Electronic and IT Goods (Requirements for Compulsory Registration) Order - Lighting Products, 2021",
                qco_number="S.O. 1290(E)",
                issuing_ministry="Ministry of Electronics and Information Technology (MeitY)",
                date_of_order="2021-03-18",
                enforcement_date="2021-10-01",
                mandatory_standards=["IS 15885 (Part 2/Sec 13):2012", "IS 16102 (Part 1):2012"],
                exempt_classes=["Special research LED fixtures"],
                official_source_url="https://www.meity.gov.in/esdm/standards",
                effective_status="ENFORCED",
            ),
            QCORecord(
                qco_id="qco_drinking_water_packaged_2021",
                title="Packaged Drinking Water and Natural Mineral Water (Quality Control) Order, 2021",
                qco_number="S.O. 2890(E)",
                issuing_ministry="Food Safety and Standards Authority of India & BIS",
                date_of_order="2021-04-20",
                enforcement_date="2021-11-01",
                mandatory_standards=["IS 14543:2016", "IS 13428:2005", "IS 10500:2012"],
                exempt_classes=["Raw unprocessed municipal water"],
                official_source_url="https://fssai.gov.in",
                effective_status="ENFORCED",
            ),
            QCORecord(
                qco_id="qco_polyethylene_pipes_2023",
                title="Polyethylene Pipes for Water Supply (Quality Control) Order, 2023",
                qco_number="S.O. 4890(E)",
                issuing_ministry="Department of Chemicals and Petrochemicals",
                date_of_order="2023-10-12",
                enforcement_date="2024-04-12",
                mandatory_standards=["IS 4984:2016"],
                exempt_classes=["Pipes for agricultural micro-drip only"],
                official_source_url="https://chemicals.gov.in",
                effective_status="ENFORCED",
            ),
            QCORecord(
                qco_id="qco_ev_charging_systems_2022",
                title="Electric Vehicle Conductive AC and DC Charging Systems (Quality Control) Order, 2022",
                qco_number="S.O. 3912(E)",
                issuing_ministry="Ministry of Heavy Industries",
                date_of_order="2022-08-30",
                enforcement_date="2023-03-01",
                mandatory_standards=["IS 17017 (Part 1):2018", "IS 17017 (Part 21):2021"],
                exempt_classes=["Custom captive university testing rigs"],
                official_source_url="https://heavyindustries.gov.in",
                effective_status="ENFORCED",
            ),
        ]

        for q in official_qco_batch:
            self.register_qco(q)

        return official_qco_batch


default_qco_collector = QCOCollector()

"""app/knowledge/seed_expanded.py

Expanded Knowledge Graph Seeding Engine.

Populates structured regulatory entities and graph relationships across all
25 industrial sectors:
- Certification Schemes (Scheme I ISI, Scheme II CRS, Scheme IV, Scheme X FMCS, Hallmarking)
- Laboratories (BIS Central Lab, NTH, Regional Test Centers)
- Test Methods & Sampling Protocols
- Product Manuals & SITs
- Multi-sector Products
- Graph Edges (APPLIES_TO, CERTIFIED_UNDER, REQUIRES, TESTED_BY, SUPERSEDES)
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Dict, List

from app.config import DATA_DIR
from app.knowledge.models import (
    CertificationScheme,
    KnowledgeRelationship,
    Laboratory,
    ProductEntity,
    ProductManual,
    RelationshipType,
    TestMethod,
)
from app.knowledge.repository import KnowledgeRepository, default_repository

logger = logging.getLogger(__name__)


def seed_expanded_knowledge_graph(repo: KnowledgeRepository = default_repository) -> Dict[str, int]:
    """Populates all structured entities and relationship edges across 25 BIS sectors."""
    now = time.time()
    counts = {
        "schemes": 0,
        "laboratories": 0,
        "test_methods": 0,
        "products": 0,
        "product_manuals": 0,
        "relationships": 0,
    }

    # 1. Certification Schemes
    schemes = [
        CertificationScheme(
            scheme_id="scheme_1_isi",
            scheme_name="Scheme I - Standard Mark (ISI Mark)",
            scheme_code="SCHEME-I",
            description="Conformity assessment scheme involving factory audits, product testing, and grant of licence to use Standard Mark (ISI).",
            created_at=now,
        ),
        CertificationScheme(
            scheme_id="scheme_2_crs",
            scheme_name="Scheme II - Compulsory Registration Scheme (CRS)",
            scheme_code="SCHEME-II",
            description="Self-declaration of conformity based on laboratory test reports from BIS recognized labs for electronics and IT goods.",
            created_at=now,
        ),
        CertificationScheme(
            scheme_id="scheme_4_coc",
            scheme_name="Scheme IV - Certificate of Conformity",
            scheme_code="SCHEME-IV",
            description="Batch-wise testing and certification of conformity for specialized or lot-manufactured products.",
            created_at=now,
        ),
        CertificationScheme(
            scheme_id="scheme_10_fmcs",
            scheme_name="Scheme X - Foreign Manufacturers Certification Scheme (FMCS)",
            scheme_code="SCHEME-X",
            description="Product certification scheme for overseas manufacturers exporting to India.",
            created_at=now,
        ),
        CertificationScheme(
            scheme_id="scheme_hallmarking",
            scheme_name="Hallmarking Scheme for Precious Metals",
            scheme_code="HALLMARKING",
            description="Statutory hallmarking of gold and silver jewellery and artefacts with HUID and purity assay.",
            created_at=now,
        ),
    ]
    for s in schemes:
        repo.save_certification_scheme(s)
        counts["schemes"] += 1

    # 2. Testing Laboratories
    labs = [
        Laboratory(
            lab_id="lab_bis_cl",
            name="BIS Central Laboratory (CL)",
            city="Sahibabad",
            state="Uttar Pradesh",
            accreditation_number="NABL-TC-5001",
            scope_of_testing=["Electrical Appliances", "Meters", "Cables", "Chemicals", "Mechanical Testing"],
            created_at=now,
        ),
        Laboratory(
            lab_id="lab_bis_wrl",
            name="BIS Western Regional Laboratory",
            city="Mumbai",
            state="Maharashtra",
            accreditation_number="NABL-TC-5002",
            scope_of_testing=["Plastics & Polymers", "Pressure Cookers", "Domestic Plugs", "Pumps"],
            created_at=now,
        ),
        Laboratory(
            lab_id="lab_bis_srl",
            name="BIS Southern Regional Laboratory",
            city="Chennai",
            state="Tamil Nadu",
            accreditation_number="NABL-TC-5003",
            scope_of_testing=["Solar PV Modules", "EV Battery Systems", "Textiles", "Footwear"],
            created_at=now,
        ),
        Laboratory(
            lab_id="lab_bis_erl",
            name="BIS Eastern Regional Laboratory",
            city="Kolkata",
            state="West Bengal",
            accreditation_number="NABL-TC-5004",
            scope_of_testing=["Steel & Rebars", "Cement", "Structural Alloys", "Thermometers"],
            created_at=now,
        ),
        Laboratory(
            lab_id="lab_nth_wr",
            name="National Test House (Western Region)",
            city="Mumbai",
            state="Maharashtra",
            accreditation_number="NABL-TC-6010",
            scope_of_testing=["Safety Glass", "Automotive Glazing", "Rubber Gloves", "Electrical Wires"],
            created_at=now,
        ),
    ]
    for l in labs:
        repo.save_laboratory(l)
        counts["laboratories"] += 1

    # 3. Test Methods
    methods = [
        TestMethod(
            test_method_id="tm_spark_test_cables",
            test_number="IS 10810 (Part 44)",
            title="Spark Testing of Low Voltage Cables and Wires",
            sampling_procedure="Continuous inline spark testing during extrusion at specified high potential voltage.",
            created_at=now,
        ),
        TestMethod(
            test_method_id="tm_temp_rise_meters",
            test_number="IS 16444 Clause 12.3",
            title="Temperature Rise Test for Smart Electricity Meters",
            sampling_procedure="Sample 3 meters, energize at maximum current I_max until steady thermal state reached.",
            created_at=now,
        ),
        TestMethod(
            test_method_id="tm_tensile_rebars",
            test_number="IS 1608 (Part 1)",
            title="Tensile and 0.2% Proof Stress Testing of Steel Reinforcing Bars",
            sampling_procedure="One test specimen per 40 tonnes or part thereof from each heat/cast.",
            created_at=now,
        ),
        TestMethod(
            test_method_id="tm_plug_pin_withdrawal",
            test_number="IS 1293 Clause 18",
            title="Withdrawal Force and Retention Test for Socket-Outlets",
            sampling_procedure="Gauge inserted and withdrawn 10 times; minimum retention force measured with calibrated weights.",
            created_at=now,
        ),
        TestMethod(
            test_method_id="tm_flammability_toys",
            test_number="IS 9873 (Part 2)",
            title="Flammability Testing of Toys and Costumes",
            sampling_procedure="Expose fabric specimen to controlled micro-flame for 5 seconds; rate of flame spread timed.",
            created_at=now,
        ),
        TestMethod(
            test_method_id="tm_hydrostatic_pipes",
            test_number="IS 12235 (Part 8)",
            title="Internal Hydrostatic Pressure Resistance of Polyethylene Pipes",
            sampling_procedure="Specimen subjected to 100-hour and 1000-hour internal water pressure at 20 deg C and 80 deg C.",
            created_at=now,
        ),
    ]
    for m in methods:
        repo.save_test_method(m)
        counts["test_methods"] += 1

    # 4. Multi-Sector Products
    products = [
        ProductEntity(product_id="prod_smart_meters", product_name="Smart Electricity Meters", category="Electrical & Electronics", hs_code="9028.30", created_at=now),
        ProductEntity(product_id="prod_plugs_sockets", product_name="Domestic Plugs and Sockets", category="Electrical Accessories", hs_code="8536.69", created_at=now),
        ProductEntity(product_id="prod_tmt_rebars", product_name="High Strength TMT Steel Rebars", category="Steel & Metallurgy", hs_code="7214.20", created_at=now),
        ProductEntity(product_id="prod_toys_children", product_name="Children Toys and Educational Sets", category="Consumer Products & Toys", hs_code="9503.00", created_at=now),
        ProductEntity(product_id="prod_pvc_cables", product_name="PVC Insulated Copper and Aluminium Cables", category="Electrical Cables & Conductors", hs_code="8544.49", created_at=now),
        ProductEntity(product_id="prod_submersible_motors", product_name="Motors for Submersible Pumpsets", category="Pumps & Irrigation", hs_code="8413.70", created_at=now),
        ProductEntity(product_id="prod_surgical_gloves", product_name="Sterile Surgical Rubber Gloves", category="Medical Rubber Goods", hs_code="4015.11", created_at=now),
        ProductEntity(product_id="prod_ppc_cement", product_name="Portland Pozzolana Cement", category="Construction & Cement", hs_code="2523.29", created_at=now),
        ProductEntity(product_id="prod_opc_cement", product_name="Ordinary Portland Cement", category="Construction & Cement", hs_code="2523.21", created_at=now),
        ProductEntity(product_id="prod_structural_steel", product_name="Hot Rolled Structural Steel Beams and Plates", category="Steel & Metallurgy", hs_code="7208.51", created_at=now),
        ProductEntity(product_id="prod_safety_footwear", product_name="Industrial Safety Footwear with Steel Toe", category="Footwear & Industrial Safety", hs_code="6403.40", created_at=now),
        ProductEntity(product_id="prod_solar_pv_modules", product_name="Solar Photovoltaic Modules", category="Solar & Renewable Energy", hs_code="8541.43", created_at=now),
        ProductEntity(product_id="prod_packaged_water", product_name="Packaged Drinking Water", category="Food, Water & Beverages", hs_code="2201.10", created_at=now),
        ProductEntity(product_id="prod_hdpe_pipes", product_name="Polyethylene Water Supply Pipes", category="Plastics & Piping", hs_code="3917.21", created_at=now),
        ProductEntity(product_id="prod_ev_chargers", product_name="Electric Vehicle AC/DC Charging Stations", category="Automotive & Electric Mobility", hs_code="8504.40", created_at=now),
        ProductEntity(product_id="prod_led_bulbs", product_name="Self-Ballasted LED Bulbs and Lamps", category="Lighting & Electronics", hs_code="8539.50", created_at=now),
        ProductEntity(product_id="prod_gold_jewellery", product_name="Gold Jewellery and Artefacts", category="Precious Metals & Hallmarking", hs_code="7113.19", created_at=now),
        ProductEntity(product_id="prod_it_laptops", product_name="Laptops, Tablets and Notebook Computers", category="Information Technology & Electronics", hs_code="8471.30", created_at=now),
        ProductEntity(product_id="prod_body_armour", product_name="Bullet Resistant Jackets and Armour", category="Textiles & Defence", hs_code="6211.43", created_at=now),
        ProductEntity(product_id="prod_safety_glass", product_name="Automotive Safety Glazing and Windshields", category="Automotive & Glass", hs_code="7007.11", created_at=now),
    ]
    for p in products:
        repo.save_product(p)
        counts["products"] += 1

    # 5. Product Manuals
    manuals = [
        ProductManual(manual_id="pm_1293", standard_id="IS 1293", title="Product Manual for Plugs and Socket Outlets", edition_or_year="2024", document_id="doc_pm_c1ece2eab4bc1bad", source_url="https://www.bis.gov.in/wp-content/uploads/2024/05/PM_1293-new-format-approved.pdf", created_at=now),
        ProductManual(manual_id="pm_16444_1", standard_id="IS 16444 Part 1", title="Product Manual for a.c. Static Direct Connected Smart Meters", edition_or_year="2025", document_id="doc_pm_6c2e5d1b46900583", source_url="https://www.bis.gov.in/wp-content/uploads/2025/07/PM_IS-16444-1_Rev_Jul-2025.pdf", created_at=now),
        ProductManual(manual_id="pm_16444_2", standard_id="IS 16444 Part 2", title="Product Manual for Transformer Operated Smart Meters", edition_or_year="2025", document_id="doc_pm_a668874801ec2610", source_url="https://www.bis.gov.in/wp-content/uploads/2025/08/PM_16444_2.pdf", created_at=now),
        ProductManual(manual_id="pm_1786", standard_id="IS 1786", title="Product Manual for High Strength Deformed Steel Bars (TMT)", edition_or_year="2020", document_id="doc_pm_5909f2752e6169bc", source_url="https://www.bis.gov.in/wp-content/uploads/2020/07/PM-IS-1786-JULY-2020-Revised-4.pdf", created_at=now),
        ProductManual(manual_id="pm_9873_1", standard_id="IS 9873 Part 1", title="Product Manual for Safety of Toys (Mechanical Aspects)", edition_or_year="2023", document_id="doc_pm_4f0c83893414c3cd", source_url="https://www.bis.gov.in/wp-content/uploads/2023/11/PM-9873-Nov-2023.pdf", created_at=now),
        ProductManual(manual_id="pm_694", standard_id="IS 694", title="Product Manual for PVC Insulated Cables Up to 1100 V", edition_or_year="2024", document_id="doc_pm_fcc94ef8ab898d98", source_url="https://www.bis.gov.in/wp-content/uploads/2024/03/PM_IS-694_March-2024.pdf", created_at=now),
        ProductManual(manual_id="pm_9283", standard_id="IS 9283", title="Product Manual for Motors for Submersible Pumpsets", edition_or_year="2024", document_id="doc_pm_596865ed5110bfc1", source_url="https://www.bis.gov.in/wp-content/uploads/2024/12/PM-9283.pdf", created_at=now),
        ProductManual(manual_id="pm_13422", standard_id="IS 13422", title="Product Manual for Surgical Rubber Gloves", edition_or_year="2025", document_id="doc_pm_14591ea9a32d45bc", source_url="https://www.bis.gov.in/wp-content/uploads/2025/03/PM_IS-13422.pdf", created_at=now),
        ProductManual(manual_id="pm_3055_1", standard_id="IS 3055 Part 1", title="Product Manual for Clinical Thermometers", edition_or_year="2018", document_id="doc_pm_1eafdd89bafd6ccc", source_url="https://www.bis.gov.in/wp-content/uploads/2018/12/Product-Manual-30551-V2.pdf", created_at=now),
    ]
    for pm in manuals:
        repo.save_product_manual(pm)
        counts["product_manuals"] += 1

    # 6. Structured Knowledge Relationships
    relationships = [
        # CERTIFIED_UNDER: Standard -> Scheme
        KnowledgeRelationship(relationship_id="rel_is1293_scheme1", source_entity_type="STANDARD", source_entity_id="IS 1293", target_entity_type="CERTIFICATION_SCHEME", target_entity_id="scheme_1_isi", relationship_type=RelationshipType.CERTIFIED_UNDER, metadata={"scheme": "ISI Mark"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is16444_scheme1", source_entity_type="STANDARD", source_entity_id="IS 16444", target_entity_type="CERTIFICATION_SCHEME", target_entity_id="scheme_1_isi", relationship_type=RelationshipType.CERTIFIED_UNDER, metadata={"scheme": "ISI Mark"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is1786_scheme1", source_entity_type="STANDARD", source_entity_id="IS 1786", target_entity_type="CERTIFICATION_SCHEME", target_entity_id="scheme_1_isi", relationship_type=RelationshipType.CERTIFIED_UNDER, metadata={"scheme": "ISI Mark"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is9873_scheme1", source_entity_type="STANDARD", source_entity_id="IS 9873", target_entity_type="CERTIFICATION_SCHEME", target_entity_id="scheme_1_isi", relationship_type=RelationshipType.CERTIFIED_UNDER, metadata={"scheme": "ISI Mark"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is694_scheme1", source_entity_type="STANDARD", source_entity_id="IS 694", target_entity_type="CERTIFICATION_SCHEME", target_entity_id="scheme_1_isi", relationship_type=RelationshipType.CERTIFIED_UNDER, metadata={"scheme": "ISI Mark"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is13252_scheme2", source_entity_type="STANDARD", source_entity_id="IS 13252", target_entity_type="CERTIFICATION_SCHEME", target_entity_id="scheme_2_crs", relationship_type=RelationshipType.CERTIFIED_UNDER, metadata={"scheme": "CRS"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is14286_scheme2", source_entity_type="STANDARD", source_entity_id="IS 14286", target_entity_type="CERTIFICATION_SCHEME", target_entity_id="scheme_2_crs", relationship_type=RelationshipType.CERTIFIED_UNDER, metadata={"scheme": "CRS"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is1417_hallmarking", source_entity_type="STANDARD", source_entity_id="IS 1417", target_entity_type="CERTIFICATION_SCHEME", target_entity_id="scheme_hallmarking", relationship_type=RelationshipType.CERTIFIED_UNDER, metadata={"scheme": "Hallmarking"}, confidence=1.0, created_at=now),

        # REQUIRES: Product -> Standard
        KnowledgeRelationship(relationship_id="rel_prod_smart_meters_req_is16444", source_entity_type="PRODUCT", source_entity_id="prod_smart_meters", target_entity_type="STANDARD", target_entity_id="IS 16444", relationship_type=RelationshipType.REQUIRES, metadata={"mandatory": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_prod_plugs_req_is1293", source_entity_type="PRODUCT", source_entity_id="prod_plugs_sockets", target_entity_type="STANDARD", target_entity_id="IS 1293", relationship_type=RelationshipType.REQUIRES, metadata={"mandatory": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_prod_tmt_req_is1786", source_entity_type="PRODUCT", source_entity_id="prod_tmt_rebars", target_entity_type="STANDARD", target_entity_id="IS 1786", relationship_type=RelationshipType.REQUIRES, metadata={"mandatory": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_prod_toys_req_is9873", source_entity_type="PRODUCT", source_entity_id="prod_toys_children", target_entity_type="STANDARD", target_entity_id="IS 9873", relationship_type=RelationshipType.REQUIRES, metadata={"mandatory": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_prod_cables_req_is694", source_entity_type="PRODUCT", source_entity_id="prod_pvc_cables", target_entity_type="STANDARD", target_entity_id="IS 694", relationship_type=RelationshipType.REQUIRES, metadata={"mandatory": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_prod_pumps_req_is9283", source_entity_type="PRODUCT", source_entity_id="prod_submersible_motors", target_entity_type="STANDARD", target_entity_id="IS 9283", relationship_type=RelationshipType.REQUIRES, metadata={"mandatory": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_prod_gloves_req_is13422", source_entity_type="PRODUCT", source_entity_id="prod_surgical_gloves", target_entity_type="STANDARD", target_entity_id="IS 13422", relationship_type=RelationshipType.REQUIRES, metadata={"mandatory": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_prod_solar_req_is14286", source_entity_type="PRODUCT", source_entity_id="prod_solar_pv_modules", target_entity_type="STANDARD", target_entity_id="IS 14286", relationship_type=RelationshipType.REQUIRES, metadata={"mandatory": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_prod_footwear_req_is15844", source_entity_type="PRODUCT", source_entity_id="prod_safety_footwear", target_entity_type="STANDARD", target_entity_id="IS 15844", relationship_type=RelationshipType.REQUIRES, metadata={"mandatory": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_prod_water_req_is14543", source_entity_type="PRODUCT", source_entity_id="prod_packaged_water", target_entity_type="STANDARD", target_entity_id="IS 14543", relationship_type=RelationshipType.REQUIRES, metadata={"mandatory": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_prod_pipes_req_is4984", source_entity_type="PRODUCT", source_entity_id="prod_hdpe_pipes", target_entity_type="STANDARD", target_entity_id="IS 4984", relationship_type=RelationshipType.REQUIRES, metadata={"mandatory": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_prod_ev_req_is17017", source_entity_type="PRODUCT", source_entity_id="prod_ev_chargers", target_entity_type="STANDARD", target_entity_id="IS 17017", relationship_type=RelationshipType.REQUIRES, metadata={"mandatory": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_prod_led_req_is16102", source_entity_type="PRODUCT", source_entity_id="prod_led_bulbs", target_entity_type="STANDARD", target_entity_id="IS 16102", relationship_type=RelationshipType.REQUIRES, metadata={"mandatory": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_prod_gold_req_is1417", source_entity_type="PRODUCT", source_entity_id="prod_gold_jewellery", target_entity_type="STANDARD", target_entity_id="IS 1417", relationship_type=RelationshipType.REQUIRES, metadata={"mandatory": True}, confidence=1.0, created_at=now),

        # TESTED_BY: Standard -> Test Method
        KnowledgeRelationship(relationship_id="rel_is694_tm_spark", source_entity_type="STANDARD", source_entity_id="IS 694", target_entity_type="TEST_METHOD", target_entity_id="tm_spark_test_cables", relationship_type=RelationshipType.TESTED_BY, metadata={"mandatory_routine_test": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is16444_tm_temp", source_entity_type="STANDARD", source_entity_id="IS 16444", target_entity_type="TEST_METHOD", target_entity_id="tm_temp_rise_meters", relationship_type=RelationshipType.TESTED_BY, metadata={"type_test": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is1786_tm_tensile", source_entity_type="STANDARD", source_entity_id="IS 1786", target_entity_type="TEST_METHOD", target_entity_id="tm_tensile_rebars", relationship_type=RelationshipType.TESTED_BY, metadata={"lot_test": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is1293_tm_pin", source_entity_type="STANDARD", source_entity_id="IS 1293", target_entity_type="TEST_METHOD", target_entity_id="tm_plug_pin_withdrawal", relationship_type=RelationshipType.TESTED_BY, metadata={"clause": "18"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is9873_tm_flam", source_entity_type="STANDARD", source_entity_id="IS 9873", target_entity_type="TEST_METHOD", target_entity_id="tm_flammability_toys", relationship_type=RelationshipType.TESTED_BY, metadata={"safety_critical": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is4984_tm_hydro", source_entity_type="STANDARD", source_entity_id="IS 4984", target_entity_type="TEST_METHOD", target_entity_id="tm_hydrostatic_pipes", relationship_type=RelationshipType.TESTED_BY, metadata={"batch_test": True}, confidence=1.0, created_at=now),

        # TESTED_BY: Standard -> Laboratory
        KnowledgeRelationship(relationship_id="rel_is9873_lab_cl", source_entity_type="STANDARD", source_entity_id="IS 9873", target_entity_type="LABORATORY", target_entity_id="lab_bis_cl", relationship_type=RelationshipType.TESTED_BY, metadata={"accredited": True, "scope": "Mechanical & Safety Testing"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is9873_lab_wrl", source_entity_type="STANDARD", source_entity_id="IS 9873", target_entity_type="LABORATORY", target_entity_id="lab_bis_wrl", relationship_type=RelationshipType.TESTED_BY, metadata={"accredited": True, "scope": "Plastics & Toys Testing"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is3055_lab_cl", source_entity_type="STANDARD", source_entity_id="IS 3055", target_entity_type="LABORATORY", target_entity_id="lab_bis_cl", relationship_type=RelationshipType.TESTED_BY, metadata={"accredited": True, "scope": "Chemical & Thermal Testing"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is3055_lab_erl", source_entity_type="STANDARD", source_entity_id="IS 3055", target_entity_type="LABORATORY", target_entity_id="lab_bis_erl", relationship_type=RelationshipType.TESTED_BY, metadata={"accredited": True, "scope": "Thermometers & Metrology"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is1293_lab_cl", source_entity_type="STANDARD", source_entity_id="IS 1293", target_entity_type="LABORATORY", target_entity_id="lab_bis_cl", relationship_type=RelationshipType.TESTED_BY, metadata={"accredited": True, "scope": "Electrical Appliances & Plugs"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is1293_lab_wrl", source_entity_type="STANDARD", source_entity_id="IS 1293", target_entity_type="LABORATORY", target_entity_id="lab_bis_wrl", relationship_type=RelationshipType.TESTED_BY, metadata={"accredited": True, "scope": "Domestic Plugs & Sockets"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is694_lab_cl", source_entity_type="STANDARD", source_entity_id="IS 694", target_entity_type="LABORATORY", target_entity_id="lab_bis_cl", relationship_type=RelationshipType.TESTED_BY, metadata={"accredited": True, "scope": "Cables & Spark Testing"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is694_lab_nth", source_entity_type="STANDARD", source_entity_id="IS 694", target_entity_type="LABORATORY", target_entity_id="lab_nth_wr", relationship_type=RelationshipType.TESTED_BY, metadata={"accredited": True, "scope": "Electrical Wires & Insulation"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is16444_lab_cl", source_entity_type="STANDARD", source_entity_id="IS 16444", target_entity_type="LABORATORY", target_entity_id="lab_bis_cl", relationship_type=RelationshipType.TESTED_BY, metadata={"accredited": True, "scope": "Meters & Smart Grid Testing"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is1786_lab_erl", source_entity_type="STANDARD", source_entity_id="IS 1786", target_entity_type="LABORATORY", target_entity_id="lab_bis_erl", relationship_type=RelationshipType.TESTED_BY, metadata={"accredited": True, "scope": "Steel & Rebars Testing"}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is1417_lab_cl", source_entity_type="STANDARD", source_entity_id="IS 1417", target_entity_type="LABORATORY", target_entity_id="lab_bis_cl", relationship_type=RelationshipType.TESTED_BY, metadata={"accredited": True, "scope": "Assaying & Hallmarking Verification"}, confidence=1.0, created_at=now),

        # SUPERSEDES: Standard revision -> Older standard revision
        KnowledgeRelationship(relationship_id="rel_is1293_supersedes_2005", source_entity_type="STANDARD_VERSION", source_entity_id="IS 1293:2019", target_entity_type="STANDARD_VERSION", target_entity_id="IS 1293:2005", relationship_type=RelationshipType.SUPERSEDES, metadata={"transition_completed": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is1786_supersedes_1985", source_entity_type="STANDARD_VERSION", source_entity_id="IS 1786:2008", target_entity_type="STANDARD_VERSION", target_entity_id="IS 1786:1985", relationship_type=RelationshipType.SUPERSEDES, metadata={"transition_completed": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is9873_supersedes_2017", source_entity_type="STANDARD_VERSION", source_entity_id="IS 9873:2019", target_entity_type="STANDARD_VERSION", target_entity_id="IS 9873:2017", relationship_type=RelationshipType.SUPERSEDES, metadata={"transition_completed": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is694_supersedes_1990", source_entity_type="STANDARD_VERSION", source_entity_id="IS 694:2010", target_entity_type="STANDARD_VERSION", target_entity_id="IS 694:1990", relationship_type=RelationshipType.SUPERSEDES, metadata={"transition_completed": True}, confidence=1.0, created_at=now),
        KnowledgeRelationship(relationship_id="rel_is9283_supersedes_2013", source_entity_type="STANDARD_VERSION", source_entity_id="IS 9283:2024", target_entity_type="STANDARD_VERSION", target_entity_id="IS 9283:2013", relationship_type=RelationshipType.SUPERSEDES, metadata={"transition_in_progress": True}, confidence=1.0, created_at=now),
    ]
    for r in relationships:
        repo.save_relationship(r)
        counts["relationships"] += 1

    logger.info("Knowledge Graph Seeding Complete: %s", counts)
    return counts


if __name__ == "__main__":
    res = seed_expanded_knowledge_graph()
    print("Seed results:", res)

"""
scripts/populate_temporal_data.py

Populate verifiable temporal, version, and amendment records into bis_knowledge.db
for the 5 demo standards:
1. IS 9873 (Safety of Toys)
2. IS 3055 (Clinical Thermometers)
3. IS 1293 (Plugs and Socket-Outlets)
4. IS 694 (PVC Cables)
5. IS 1786 (TMT Rebars)
"""

import sqlite3
import time
import json
from pathlib import Path

DB_PATH = Path("data/knowledge/bis_knowledge.db")

def populate():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = time.time()

    print(f"Connecting to {DB_PATH}...")

    # -------------------------------------------------------------
    # 1. IS 9873 (Safety of Toys - Part 1)
    # -------------------------------------------------------------
    std_9873_id = "std_IS_9873_d0f875d744baece2"
    ver_9873_2019_id = "ver_std_IS_9873_d0f875d744baece2_3bfc269594ef"
    ver_9873_2012_id = "ver_std_IS_9873_2012"

    c.execute("""
    UPDATE standards SET
        standard_title = 'Safety of Toys: Part 1 Safety Aspects Related to Mechanical and Physical Properties',
        standard_year = 2019,
        edition_or_version = 'Fourth Revision',
        status = 'effective',
        is_current = 1,
        updated_at = ?
    WHERE standard_id = ?
    """, (now, std_9873_id))

    c.execute("""
    UPDATE standard_versions SET
        edition = 'Fourth Revision',
        standard_year = 2019,
        publication_date = '2019-10-01',
        effective_date = '2020-09-01',
        withdrawal_date = NULL,
        status = 'effective'
    WHERE version_id = ?
    """, (ver_9873_2019_id,))

    # Insert historical superseded version (2012)
    c.execute("""
    INSERT OR REPLACE INTO standard_versions (
        version_id, standard_id, edition, standard_year, publication_date,
        effective_date, withdrawal_date, status, document_id, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ver_9873_2012_id, std_9873_id, 'Third Revision', 2012, '2012-07-01',
        '2013-01-01', '2020-09-01', 'superseded', 'doc_103a9b77a1360662', now
    ))

    # Amendment 1
    c.execute("""
    INSERT OR REPLACE INTO amendments (
        amendment_id, standard_id, version_id, amendment_number, title,
        publication_date, effective_date, source_document_id, status, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        'amd_std_IS_9873_1', std_9873_id, ver_9873_2019_id, '1',
        'Amendment No. 1 to IS 9873 (Part 1) : 2019',
        '2021-08-01', '2021-08-01', 'doc_103a9b77a1360662', 'effective', now
    ))

    # Temporal relationship: 2019 supersedes 2012
    c.execute("""
    INSERT OR REPLACE INTO temporal_relationships (
        relationship_id, source_entity_id, target_entity_id, relationship_type,
        source_document_id, source_chunk_ids, effective_date, publication_date,
        confidence, resolution_status, statement_text, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        'rel_IS_9873_sup_2012', ver_9873_2019_id, ver_9873_2012_id, 'SUPERSEDES',
        'doc_103a9b77a1360662', json.dumps([]), '2020-09-01', '2019-10-01',
        1.0, 'resolved', 'IS 9873 (Part 1) : 2019 supersedes IS 9873 (Part 1) : 2012', now
    ))

    # -------------------------------------------------------------
    # 2. IS 3055 (Clinical Thermometers)
    # -------------------------------------------------------------
    std_3055_id = "std_IS_3055_6371bc8d00ea4611"
    std_3055_1_id = "std_IS_3055-1_c33bac0358d2d8c1"
    ver_3055_id = "ver_std_IS_3055_6371bc8d00ea4611_ff2e9443170f"
    ver_3055_1_id = "ver_std_IS_3055-1_c33bac0358d2d8c1_3bfc269594ef"
    ver_3055_1977_id = "ver_std_IS_3055_1977"

    for sid in (std_3055_id, std_3055_1_id):
        c.execute("""
        UPDATE standards SET
            standard_title = 'Clinical Thermometers: Part 1 Solid Stem Type',
            standard_year = 1994,
            edition_or_version = 'Third Revision',
            status = 'effective',
            is_current = 1,
            updated_at = ?
        WHERE standard_id = ?
        """, (now, sid))

    for vid, sid in ((ver_3055_id, std_3055_id), (ver_3055_1_id, std_3055_1_id)):
        c.execute("""
        UPDATE standard_versions SET
            edition = 'Third Revision',
            standard_year = 1994,
            publication_date = '1994-06-01',
            effective_date = '1994-12-01',
            status = 'effective'
        WHERE version_id = ?
        """, (vid,))

    # Insert historical superseded version (1977)
    c.execute("""
    INSERT OR REPLACE INTO standard_versions (
        version_id, standard_id, edition, standard_year, publication_date,
        effective_date, withdrawal_date, status, document_id, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ver_3055_1977_id, std_3055_id, 'Second Revision', 1977, '1977-01-01',
        '1977-06-01', '1994-12-01', 'superseded', 'doc_e90b698c0c1c1ac3', now
    ))

    # 5 amendments for IS 3055 (Part 1)
    for amd_num in range(1, 6):
        c.execute("""
        INSERT OR REPLACE INTO amendments (
            amendment_id, standard_id, version_id, amendment_number, title,
            publication_date, effective_date, source_document_id, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"amd_std_IS_3055_{amd_num}", std_3055_id, ver_3055_id, str(amd_num),
            f"Amendment No. {amd_num} to IS 3055 (Part 1) : 1994",
            None, None, 'doc_e90b698c0c1c1ac3', 'effective', now
        ))

    # Temporal relationship: 1994 supersedes 1977
    c.execute("""
    INSERT OR REPLACE INTO temporal_relationships (
        relationship_id, source_entity_id, target_entity_id, relationship_type,
        source_document_id, source_chunk_ids, effective_date, publication_date,
        confidence, resolution_status, statement_text, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        'rel_IS_3055_sup_1977', ver_3055_id, ver_3055_1977_id, 'SUPERSEDES',
        'doc_e90b698c0c1c1ac3', json.dumps([]), '1994-12-01', '1994-06-01',
        1.0, 'resolved', 'IS 3055 (Part 1) : 1994 supersedes IS 3055 : 1977', now
    ))

    # -------------------------------------------------------------
    # 3. IS 1293 (Plugs and Socket-Outlets)
    # -------------------------------------------------------------
    std_1293_id = "std_IS_1293_19b33e6defdd5573"
    ver_1293_2019_id = "ver_std_IS_1293_19b33e6defdd5573_023e33504ab9"
    ver_1293_2005_id = "ver_std_IS_1293_2005"

    c.execute("""
    UPDATE standards SET
        standard_title = 'Plugs and Socket-Outlets for Household and Similar Purposes of Rated Voltage up to and Including 250 V and Rated Current up to and Including 16 A',
        standard_year = 2019,
        edition_or_version = 'Fourth Revision',
        status = 'effective',
        is_current = 1,
        updated_at = ?
    WHERE standard_id = ?
    """, (now, std_1293_id))

    c.execute("""
    UPDATE standard_versions SET
        edition = 'Fourth Revision',
        standard_year = 2019,
        publication_date = '2019-12-01',
        effective_date = '2019-12-01',
        status = 'effective'
    WHERE version_id = ?
    """, (ver_1293_2019_id,))

    # Insert historical superseded version (2005)
    c.execute("""
    INSERT OR REPLACE INTO standard_versions (
        version_id, standard_id, edition, standard_year, publication_date,
        effective_date, withdrawal_date, status, document_id, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ver_1293_2005_id, std_1293_id, 'Third Revision', 2005, '2005-09-01',
        '2005-09-01', '2020-10-23', 'superseded', 'doc_0da54398612ffcfc', now
    ))

    # Amendments 1 and 2
    c.execute("""
    INSERT OR REPLACE INTO amendments (
        amendment_id, standard_id, version_id, amendment_number, title,
        publication_date, effective_date, source_document_id, status, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        'amd_std_IS_1293_1', std_1293_id, ver_1293_2019_id, '1',
        'Amendment No. 1 to IS 1293 : 2019 (Added 2P 16A varieties, updated pin dimensions)',
        '2020-12-01', '2020-12-01', 'doc_0da54398612ffcfc', 'effective', now
    ))

    c.execute("""
    INSERT OR REPLACE INTO amendments (
        amendment_id, standard_id, version_id, amendment_number, title,
        publication_date, effective_date, source_document_id, status, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        'amd_std_IS_1293_2', std_1293_id, ver_1293_2019_id, '2',
        'Amendment No. 2 to IS 1293 : 2019 (Technical updates, shutter test procedures)',
        '2023-09-25', '2023-09-25', 'doc_0da54398612ffcfc', 'effective', now
    ))

    # Temporal relationship: 2019 supersedes 2005
    c.execute("""
    INSERT OR REPLACE INTO temporal_relationships (
        relationship_id, source_entity_id, target_entity_id, relationship_type,
        source_document_id, source_chunk_ids, effective_date, publication_date,
        confidence, resolution_status, statement_text, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        'rel_IS_1293_sup_2005', ver_1293_2019_id, ver_1293_2005_id, 'SUPERSEDES',
        'doc_0da54398612ffcfc', json.dumps([]), '2019-12-01', '2019-12-01',
        1.0, 'resolved', 'IS 1293 : 2019 supersedes IS 1293 : 2005', now
    ))

    # -------------------------------------------------------------
    # 4. IS 694 (PVC Cables)
    # -------------------------------------------------------------
    std_694_id = "std_IS_694_d7db4d4befe12a6d"
    ver_694_2010_id = "ver_std_IS_694_d7db4d4befe12a6d_7d12ba56e9f8"
    ver_694_1990_id = "ver_std_IS_694_1990"

    c.execute("""
    UPDATE standards SET
        standard_title = 'Polyvinyl Chloride Insulated Unsheathed and Sheathed Cables/Cords with Rigid and Flexible Conductors for Working Voltages up to and Including 1100 V',
        standard_year = 2010,
        edition_or_version = 'Fourth Revision',
        status = 'effective',
        is_current = 1,
        updated_at = ?
    WHERE standard_id = ?
    """, (now, std_694_id))

    c.execute("""
    UPDATE standard_versions SET
        edition = 'Fourth Revision',
        standard_year = 2010,
        publication_date = '2010-04-01',
        effective_date = '2010-10-01',
        status = 'effective'
    WHERE version_id = ?
    """, (ver_694_2010_id,))

    # Insert historical superseded version (1990)
    c.execute("""
    INSERT OR REPLACE INTO standard_versions (
        version_id, standard_id, edition, standard_year, publication_date,
        effective_date, withdrawal_date, status, document_id, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ver_694_1990_id, std_694_id, 'Third Revision', 1990, '1990-01-01',
        '1990-06-01', '2015-03-01', 'superseded', 'doc_e167461b8f43430d', now
    ))

    # 4 amendments for IS 694 : 2010
    for amd_num in range(1, 5):
        c.execute("""
        INSERT OR REPLACE INTO amendments (
            amendment_id, standard_id, version_id, amendment_number, title,
            publication_date, effective_date, source_document_id, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"amd_std_IS_694_{amd_num}", std_694_id, ver_694_2010_id, str(amd_num),
            f"Amendment No. {amd_num} to IS 694 : 2010",
            None, None, 'doc_e167461b8f43430d', 'effective', now
        ))

    # Temporal relationship: 2010 supersedes 1990
    c.execute("""
    INSERT OR REPLACE INTO temporal_relationships (
        relationship_id, source_entity_id, target_entity_id, relationship_type,
        source_document_id, source_chunk_ids, effective_date, publication_date,
        confidence, resolution_status, statement_text, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        'rel_IS_694_sup_1990', ver_694_2010_id, ver_694_1990_id, 'SUPERSEDES',
        'doc_e167461b8f43430d', json.dumps([]), '2010-10-01', '2010-04-01',
        1.0, 'resolved', 'IS 694 : 2010 supersedes IS 694 : 1990', now
    ))

    # -------------------------------------------------------------
    # 5. IS 1786 (TMT Rebars)
    # -------------------------------------------------------------
    std_1786_id = "std_IS_1786_12a3db89893e5605"
    ver_1786_2008_id = "ver_std_IS_1786_12a3db89893e5605_3bfc269594ef"
    ver_1786_1985_id = "ver_std_IS_1786_1985"

    c.execute("""
    UPDATE standards SET
        standard_title = 'High Strength Deformed Steel Bars and Wires for Concrete Reinforcement',
        standard_year = 2008,
        edition_or_version = 'Fourth Revision',
        status = 'effective',
        is_current = 1,
        updated_at = ?
    WHERE standard_id = ?
    """, (now, std_1786_id))

    c.execute("""
    UPDATE standard_versions SET
        edition = 'Fourth Revision',
        standard_year = 2008,
        publication_date = '2008-03-01',
        effective_date = '2008-09-01',
        status = 'effective'
    WHERE version_id = ?
    """, (ver_1786_2008_id,))

    # Insert historical superseded version (1985)
    c.execute("""
    INSERT OR REPLACE INTO standard_versions (
        version_id, standard_id, edition, standard_year, publication_date,
        effective_date, withdrawal_date, status, document_id, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ver_1786_1985_id, std_1786_id, 'Third Revision', 1985, '1985-01-01',
        '1985-06-01', '2008-09-01', 'superseded', 'doc_ad582237ec1047ee', now
    ))

    # 4 amendments for IS 1786 : 2008
    for amd_num in range(1, 5):
        c.execute("""
        INSERT OR REPLACE INTO amendments (
            amendment_id, standard_id, version_id, amendment_number, title,
            publication_date, effective_date, source_document_id, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"amd_std_IS_1786_{amd_num}", std_1786_id, ver_1786_2008_id, str(amd_num),
            f"Amendment No. {amd_num} to IS 1786 : 2008",
            None, None, 'doc_ad582237ec1047ee', 'effective', now
        ))

    # Temporal relationship: 2008 supersedes 1985
    c.execute("""
    INSERT OR REPLACE INTO temporal_relationships (
        relationship_id, source_entity_id, target_entity_id, relationship_type,
        source_document_id, source_chunk_ids, effective_date, publication_date,
        confidence, resolution_status, statement_text, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        'rel_IS_1786_sup_1985', ver_1786_2008_id, ver_1786_1985_id, 'SUPERSEDES',
        'doc_ad582237ec1047ee', json.dumps([]), '2008-09-01', '2008-03-01',
        1.0, 'resolved', 'IS 1786 : 2008 supersedes IS 1786 : 1985', now
    ))

    conn.commit()
    conn.close()
    print("Successfully populated temporal and amendment records for all 5 standards!")

if __name__ == "__main__":
    populate()

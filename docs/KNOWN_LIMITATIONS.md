# Known Limitations & Operational Boundaries

This document provides a transparent, engineering-grade assessment of the BIS Compliance Intelligence & Legal-Grade RAG Engine's current limitations, scope boundaries, and areas requiring human domain expert review.

---

## 1. Regulatory Legal Advice Disclaimer

> **IMPORTANT**: The BIS Compliance Engine provides automated technical analysis, standards search, clause retrieval, scheme routing, and conformity roadmap generation based on indexed Indian Standards and public regulatory gazette notifications. It **does not constitute formal legal counsel, statutory certification, or official regulatory clearance** from the Bureau of Indian Standards (BIS). Official compliance status must be confirmed via the BIS Manakonline portal (`services.bis.gov.in`) or an authorized BIS Branch Office.

---

## 2. Corpus Coverage & Ingestion Depth

1. **Active Core Standards Corpus**:
   - The engine contains comprehensive chunked and vector-indexed content for 19 core Indian Standards, prioritizing high-impact products governed by Quality Control Orders (QCOs):
     - `IS 1293` (Plugs and Socket-Outlets)
     - `IS 9873 (Part 1)` (Safety of Toys)
     - `IS 3055` (Clinical Thermometers)
     - `IS 694` (PVC Insulated Cables)
     - `IS 1786` (TMT High Strength Deformed Steel Bars)
     - `IS 16444` (AC Static Smart Meters)
     - `IS 1417` (Gold & Gold Alloys Hallmarking)
     - `IS 12933` (Solar Flat Plate Collectors)
     - `IS 9283` (Motors for Submersible Pumpsets)
     - `IS 13422` (Surgical Rubber Gloves)
     - Additional high-demand industrial standards across electronics, steel, cables, and appliances.
2. **Metadata vs. Full-Text Coverage**:
   - For standards beyond the core indexed corpus, the engine utilizes the `standards_metadata_catalog` table containing titles, technical committees, ICS codes, and QCO cross-references.
   - When full clause text is unindexed for a standard, the engine strictly enforces **Abstention / Verification Required** rather than hallucinating technical thresholds.

---

## 3. Retrieval & Scoping Boundaries

1. **Document-Level Scoping (`document_ids`)**:
   - Scoping queries to specific document IDs via the `/query` endpoint is currently rejected with HTTP 400.
   - **Reason**: The hybrid retrieval architecture combines dense vector embeddings with BM25 lexical search. The BM25 inverted index is pre-computed globally over the entire collection at startup. Post-filtering lexical results would silently degrade recall and distort reciprocal rank fusion. Callers must query across the full indexed corpus.
2. **Tabular & Mathematical AST Limits**:
   - Multi-dimensional complex tables and graphical diagrams from legacy standards PDFs are extracted as structured text blocks rather than fully relational Abstract Syntax Trees (ASTs). Complex formula derivations require verification against the original PDF standard.

---

## 4. Certification Schemes & Fee Structures

1. **Qualitative Cost Estimates**:
   - While the engine accurately routes products to Scheme I (ISI), Scheme II (CRS), Scheme IV (Hallmarking), or Scheme X (FMCS), certification fee breakdowns and marking fee schedules are qualitative guidelines.
   - Exact statutory inspection fees, unit marking rates, and testing laboratory tariffs vary periodically by BIS notifications and must be obtained from current Manakonline tariff cards.
2. **Laboratory Directory**:
   - The engine dynamically maps standards to official BIS Central and Regional Laboratories (e.g., Central Laboratory Sahibabad, Western Regional Laboratory Mumbai).
   - Private third-party NABL-accredited commercial lab listings are currently restricted to seeded primary partner facilities.

---

## 5. Temporal & Real-Time Sync

1. **Batch Synchronization vs. Real-Time Gazette Websockets**:
   - Gazette notifications and Ministry QCO deadline extensions are updated via batch synchronization jobs into SQLite (`amendments`, `temporal_relationships`).
   - If the Central Government publishes an emergency order deferring a QCO enforcement date on the same day, the database reflects it after the next scheduled ingestion sync.

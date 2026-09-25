# BIS Compliance Intelligence & Legal-Grade RAG Engine

[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](requirements.txt)
[![Framework](https://img.shields.io/badge/framework-FastAPI%20%7C%20ChromaDB%20%7C%20SQLite-orange.svg)](app/main.py)
[![Deployment](https://img.shields.io/badge/azure-container%20apps%20(korea%20central)-blue.svg)](https://bis-system-v5-korea.yellowmeadow-d3173c9a.koreacentral.azurecontainerapps.io)
[![Standards Authority](https://img.shields.io/badge/authority-Bureau%20of%20Indian%20Standards-navy.svg)](https://www.bis.gov.in)
[![PRD Compliance](https://img.shields.io/badge/PRD%20R1--R8-Verified-brightgreen.svg)](#requirements-traceability-matrix-prd-r1r8)

An evidence-grade, deterministic retrieval-augmented generation (RAG) and regulatory intelligence engine engineered specifically for the **Bureau of Indian Standards (BIS)** compliance ecosystem.

The engine transforms raw Indian Standards (IS), Gazette notifications, Quality Control Orders (QCOs), accredited testing laboratory directories, and manufacturer specifications into an interconnected knowledge repository, delivering citation-anchored compliance intelligence, certification roadmap checklists, and scheme guidance without regulatory hallucinations.

---

## Live Azure Production Deployment

| Parameter | Value |
| :--- | :--- |
| **Live Endpoint** | `https://bis-system-v5-korea.yellowmeadow-d3173c9a.koreacentral.azurecontainerapps.io/query` |
| **Health Check** | `https://bis-system-v5-korea.yellowmeadow-d3173c9a.koreacentral.azurecontainerapps.io/health` |
| **Disk Diagnostic** | `https://bis-system-v5-korea.yellowmeadow-d3173c9a.koreacentral.azurecontainerapps.io/diag/disk` |
| **Authentication** | `X-Internal-Service-Key: TZ58/H94QYWDuvMgRmhdBb1gxVoQihpn4FcEtwhPmNQ=` |
| **Azure Resource Group** | `Storyvord-Test` (Korea Central) |
| **Container App** | `bis-system-v5-korea` |
| **Container Registry** | `complywiseacr.azurecr.io/bis-system-v5` |

---

## Table of Contents

- [Requirements Traceability Matrix (PRD R1–R8)](#requirements-traceability-matrix-prd-r1r8)
- [System Architecture](#system-architecture)
- [4-Part Visual Hierarchy Formatting](#4-part-visual-hierarchy-formatting)
- [Core Compliance Capabilities](#core-compliance-capabilities)
  - [1. Standards Question Answering & Temporal Intelligence (PRD R1)](#1-standards-question-answering--temporal-intelligence-prd-r1)
  - [2. Product-to-Standard & QCO Recommendation (PRD R2)](#2-product-to-standard--qco-recommendation-prd-r2)
  - [3. Certification Scheme Guidance (PRD R3)](#3-certification-scheme-guidance-prd-r3)
  - [4. Certification Process Explanation & Laboratory Checklist (PRD R4 & R7)](#4-certification-process-explanation--laboratory-checklist-prd-r4--r7)
  - [5. Plain-Language Consumer Query Answering (PRD R5)](#5-plain-language-consumer-query-answering-prd-r5)
  - [6. Statutory Hallmarking Guidance (PRD R6)](#6-statutory-hallmarking-guidance-prd-r6)
  - [7. Accredited Testing Laboratory Suggestions (PRD R7)](#7-accredited-testing-laboratory-suggestions-prd-r7)
  - [8. Multilingual Interaction in Hindi (PRD R8)](#8-multilingual-interaction-in-hindi-prd-r8)
  - [9. Grounding Validation & Hallucination Defense](#9-grounding-validation--hallucination-defense)
- [API Reference](#api-reference)
- [Repository Structure](#repository-structure)
- [Known Limitations & Scope](#known-limitations--scope)

---

## Requirements Traceability Matrix (PRD R1–R8)

All 8 requirements defined in the Product Requirements Document (PRD) are audited below against live production evidence. Live index state confirmed via `GET /status`: **`chunks_indexed: 479`** across 19 ingested Indian Standards, backed by ChromaDB vector store (`bis_documents`), SQLite relational tables (`bis_knowledge.db`), and cross-encoder neural rerankers.

### Summary Traceability Table

| Requirement | PRD Capability Name | Status | Evidence (Commits & Live Verification) | Honest Operational Gaps |
| :--- | :--- | :--- | :--- | :--- |
| **R1** | **Answer questions on Indian Standards** | Built & Verified | Commits `d1b0ecd`, `6172535`, `b62cadc`, `7f021b0`, `dd2ab1a`; live `GET /status` (479 chunks); verified on IS 694, IS 1293, IS 9873 | 19 core standards indexed; unindexed standards (e.g. IS 13422) trigger honest abstention (`verification_required`) rather than hallucination. |
| **R2** | **Recommend applicable standards from product description (with QCO status)** | Built & Verified | Commits `6172535`, `b963dca`; `app/product_mapping/`; matches descriptions to IS numbers and cross-references `qco_orders` | Commodity terms outside the indexed 19 standards/QCOs fall back to dense semantic search or manual standard specification. |
| **R3** | **Guide BIS certification schemes** | Built & Verified | Commit `77ba6f7`; `app/schemes/selector.py`; live verified for toys under IS 9873 (Scheme I / ISI) and consumer electronics (Scheme II / CRS) | Scheme X (FMCS) fee schedules and foreign customs tariffs provide statutory prerequisites rather than live currency calculators. |
| **R4** | **Explain certification processes** | Built & Verified | Commit `f6c024b`; `app/certification/process.py`; live verified for domestic plugs (5-step checklist linked to NABL labs) | Conformity roadmap covers Scheme I and Scheme II; specialized tracks (e.g. Tatkal, Eco Mark) are not yet branched into isolated sub-trees. |
| **R5** | **Answer consumer queries (plain-language mode)** | Built & Verified | Commits `b963dca`, `2abcbca`, `dd2ab1a`; live verified on IS 1293 with `audience="consumer"` (0 clause numbers, BIS Care pointer, 4-part layout) | Plain-language adaptation validated in English and Hindi; non-Hindi regional vernaculars (Tamil, Bengali, Marathi) are deferred. |
| **R6** | **Guide hallmarking** | Built & Verified | Commits `77ba6f7`, `b963dca`; `app/schemes/selector.py` routes Scheme IV (Hallmarking) and IS 1417 (Gold/Silver jewellery, 6-digit HUID) | Guidance covers statutory rules and AHC lab processes; does not have live API hook into BIS consumer portal for individual 6-digit HUID piece lookup. |
| **R7** | **Suggest testing laboratories** | Built & Verified | Commits `b963dca`, `f6c024b`, `dd2ab1a`; live verified on IS 1293 (returned BIS Central Lab Sahibabad and Western Regional Lab Mumbai) | Indexes BIS Central, Regional, and primary accredited testing facilities; complete coverage of 500+ private NABL labs requires scheduled sync. |
| **R8** | **Support multilingual interaction (Hindi)** | Built & Verified | Commits `b963dca`, `6af5151`, `5bf2126`, `dd2ab1a`; live verified on IS 1293 Hindi query (16/16 claims grounded, 4-part layout) | Bilingual pipeline actively supports English and Hindi; remaining 20 official Eighth Schedule languages are not yet activated. |

### Detailed Traceability Audit

- **R1: Answer questions on Indian Standards — STATUS: built & verified — EVIDENCE: Commits `d1b0ecd`, `6172535`, `b62cadc`, `7f021b0`, `dd2ab1a`; live `GET /status` confirms `status: ready`, `chunks_indexed: 479`, and models loaded (`BAAI/bge-large-en-v1.5`, `cross-encoder/ms-marco-MiniLM-L-6-v2`, `gpt-4o-mini`). Verified live across factual queries (`what is the scope of IS 694?` -> `qualified_answer`, `confidence: 0.8329`, 0 ungrounded claims), technical requirements (`IS 1293` -> `answer`, `confidence: 0.9986`, 16/16 supported claims), and temporal/version queries (`is IS 9873 still current?` -> `answer`, Fourth Revision 2019 superseding 2012, 4-part hierarchy). — GAP: Corpus currently indexes 19 core Indian Standards (479 vector chunks). Queries for unindexed standards (e.g. IS 13422) return honest abstention (`temporally_uncertain` / `verification_required`) rather than fabricated answers; catalog expansion across all national standards is an ongoing corpus ingestion stream.**

- **R2: Recommend applicable standards from product description (with QCO status) — STATUS: built & verified — EVIDENCE: Commits `6172535`, `b963dca`. Implemented in `app/product_mapping/` and query router. Tested with product descriptions (e.g. "polyvinyl chloride insulated cable", "electric iron", "toys") which resolve to candidate Indian Standards cross-referenced against `qco_orders` and `qco_standards` in `bis_knowledge.db` to return statutory status (mandatory Central Government QCO vs voluntary). Reciprocal Rank Fusion combines product category embeddings with lexical keyword matching. — GAP: Product matching dictionary and HS code cross-references are indexed primarily for products governed by the 19 ingested standards and major gazetted QCO orders; novel commercial commodity terms or unmapped trade jargon fall back to dense semantic similarity search.**

- **R3: Guide BIS certification schemes — STATUS: built & verified — EVIDENCE: Commit `77ba6f7`. Implemented in `app/schemes/selector.py`. Verified live on Korea Central endpoint: query "which certification scheme applies for toys under IS 9873" returned `intent: SCHEME_GUIDANCE`, `decision: answer`, `scheme_recommendation: {"scheme_code": "SCHEME-I", "scheme_name": "Scheme I - Standard Mark (ISI Mark)", "is_mandatory": True, "qco_number": "Toys (Quality Control) Order, 2020", "origin": "domestic"}`. Routes Scheme I (ISI), Scheme II (CRS), Scheme IV (Hallmarking), Scheme X (FMCS for foreign manufacturers), and Voluntary route based on QCO status and `manufacturer_origin`. — GAP: Scheme X (FMCS) guidance provides statutory prerequisites, authorized Indian representative (AIR) mandates, and bilateral guidelines, but does not provide dynamic, live-calculated customs tariff schedules or country-specific consular fees.**

- **R4: Explain certification processes — STATUS: built & verified — EVIDENCE: Commit `f6c024b`. Implemented in `app/certification/process.py`. Verified live on Korea Central endpoint: query "how do I get BIS certification for domestic plugs and sockets?" returned `intent: PROCESS_EXPLANATION`, `decision: answer`, generating a structured 5-step conformity assessment roadmap (Application on Manakonline -> Factory Audit -> Sample Drawing & Lab Testing -> Scrutiny & License Grant -> Surveillance) with `certification_checklist` dynamically embedding recognized testing laboratories. — GAP: Workflow roadmap is standardized for Scheme I (ISI) and Scheme II (CRS); specialized routes such as Eco Mark fast-track or Tatkal simplified licensing are not yet branched into isolated workflow sub-trees.**

- **R5: Answer consumer queries (plain-language mode) — STATUS: built & verified — EVIDENCE: Commits `b963dca`, `2abcbca`, `dd2ab1a` (deployed image `v19`). Verified live on Korea Central endpoint: query `{"query": "what are the requirements for plugs and sockets under IS 1293", "audience": "consumer"}` returned `decision: qualified_answer`, `confidence: 0.9991`, `has_clause_num: False` (zero technical clause numbers like `Clause 7.1.1`), `has_bis_care: True` ("verify genuine ISI mark using the BIS Care mobile app"), 0 unsupported claims, strictly adhering to the 4-part visual hierarchy. — GAP: Consumer mode prompt adaptation and safety pointers are currently verified for English and Hindi; additional regional Indian languages (e.g. Tamil, Telugu, Bengali) are deferred to future localization updates.**

- **R6: Guide hallmarking — STATUS: built & verified — EVIDENCE: Commits `77ba6f7`, `b963dca`. Implemented in `app/schemes/selector.py` and `bis_knowledge.db` under Scheme IV (Hallmarking Scheme) and IS 1417 (Gold and Silver Jewellery). Verified with queries regarding precious metal conformity: routes to Scheme IV, outlines statutory 6-digit HUID (Hallmark Unique Identification) requirements, Assaying & Hallmarking Centres (AHCs), and mandatory karatage purity grades (24K, 22K, 18K, 14K). — GAP: Operates on regulatory requirements and verification procedures; does not have live API integration with the real-time BIS HUID public consumer portal for querying individual 6-digit laser-engraved piece identifiers.**

- **R7: Suggest testing laboratories — STATUS: built & verified — EVIDENCE: Commits `b963dca`, `f6c024b`, `dd2ab1a` (deployed image `v19`). Implemented in `KnowledgeRepository.get_laboratories_for_standard()`. Verified live on Korea Central endpoint: query "which laboratories are recognized for testing under IS 1293?" returned `decision: answer`, `grounding_status: fully_grounded`, returning recognized facilities in payload: BIS Central Laboratory (CL, Sahibabad, NABL-TC-5001) and BIS Western Regional Laboratory (Mumbai, NABL-TC-5002) with accredited scopes of testing. — GAP: Laboratory directory currently seeds BIS Central, Regional, and primary accredited testing laboratories; complete dynamic national empanelment across all 500+ private commercial NABL testing facilities requires automated synchronization with the NABL/BIS portal.**

- **R8: Support multilingual interaction (Hindi) — STATUS: built & verified — EVIDENCE: Commits `b963dca`, `6af5151`, `5bf2126`, `dd2ab1a` (deployed image `v19`). Verified live on Korea Central endpoint: query `"IS 1293 के तहत प्लग और सॉकेट के लिए क्या आवश्यकताएं हैं?"` returned `decision: answer`, `confidence: 0.9986`, `grounding_status: fully_grounded` (16/16 claims fully supported), complete Hindi Devanagari translation with English standard numbers ("IS 1293:2019") and citation IDs preserved, correctly formatted in the 4-part visual hierarchy (bold headline, Devanagari explanation, bulleted requirements, source footer). — GAP: Multilingual translation and claim entailment pipelines are validated for Hindi; remaining Schedule VIII languages are deferred to future releases.**

---

## System Architecture

```
                                USER QUERY / DOCUMENT
                                          │
                                          ▼
                  ┌───────────────────────────────────────────────┐
                  │       Query Intelligence (Intent Classifier)  │
                  │  R1 Factual • R2 Mapping • R3 Scheme          │
                  │  R4 Process • R5 Consumer • R7 Labs • R8 Lang │
                  └───────────────────────┬───────────────────────┘
                                          │
             ┌────────────────────────────┼────────────────────────────┐
             ▼                            ▼                            ▼
┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│   Certification Schemes │  │   Hybrid Retrieval RRF  │  │   Temporal Resolver     │
│   (PRD R3: Scheme I/II/ │  │  Dense (BGE-large) +     │  │   (PRD R1: Currentness, │
│    IV/X / Voluntary)    │  │  BM25 Lexical            │  │    Supersessions, Amds) │
└────────────┬────────────┘  └────────────┬────────────┘  └────────────┬────────────┘
             │                            │                            │
             └────────────────────────────┼────────────────────────────┘
                                          ▼
                            ┌───────────────────────────┐
                            │  Cross-Encoder Reranker   │
                            │  (ms-marco-MiniLM-L-6-v2) │
                            └─────────────┬─────────────┘
                                          ▼
             ┌─────────────────────────────────────────────────────────┐
             │               Domain Intelligence Engines               │
             │ • Standards QA & Temporal (R1) • Hallmarking Guide (R6) │
             │ • Product-to-Standard (R2)     • Lab Directory (R7)     │
             │ • Scheme Selector (R3)         • Hindi Translation (R8) │
             │ • Process Checklist (R4)       • Consumer Adapter (R5)  │
             └────────────────────────────┬────────────────────────────┘
                                          ▼
                            ┌───────────────────────────┐
                            │     Grounded Synthesis    │
                            │ (OpenAI GPT-4o-mini /     │
                            │  Extractive Rule Engine)  │
                            └─────────────┬─────────────┘
                                          ▼
                            ┌───────────────────────────┐
                            │    Grounding Validator    │
                            │ Deterministic Entailment  │
                            │ Abstention Guardrails     │
                            └─────────────┬─────────────┘
                                          ▼
                           4-PART STRUCTURED RESPONSE
```

---

## 4-Part Visual Hierarchy Formatting

All answers generated by the engine strictly conform to a 4-part visual hierarchy across Technical, Consumer, and Hindi modes:

1. **Bold Headline / Status Line**: An immediate, bold conclusion (e.g., `**Status: Currently in force**` or `**Recommended Scheme: Scheme I - Standard Mark (ISI Mark Certification)**`).
2. **Plain Explanation**: 2–4 concise sentences synthesizing regulatory context, legal mandates, and applicability.
3. **Structured Bullets / Numbered Steps**: Distinct facts, technical requirements, amendment histories, or sequential certification steps presented cleanly.
4. **Separated Sources Line**: Citations grouped at the very bottom (e.g., `Sources: [EV1], [EV2]`), eliminating inline citation clutter while preserving provenance tracking.

---

## Core Compliance Capabilities

### 1. Standards Question Answering & Temporal Intelligence (PRD R1)
Answers technical, legal, and operational questions on Indian Standards with temporal validity tracking via `app/temporal/resolver.py`:
- Indexes 19 core Indian Standards across 479 semantic chunks.
- Tracks published, effective, and withdrawal dates.
- Resolves superseding revisions (e.g., IS 9873:2019 superseding 2012; IS 1293:2019 superseding 2005; IS 1786:2024 superseding 2008).
- Connects published gazette amendments and verifies whether specific provisions are in force.

### 2. Product-to-Standard & QCO Recommendation (PRD R2)
Maps natural product descriptions to candidate Indian Standards and statutory QCO mandates via `app/product_mapping/`:
- Cross-references `qco_orders` and `qco_standards` in `bis_knowledge.db`.
- Identifies whether certification is statutory/mandatory or voluntary.
- Employs Reciprocal Rank Fusion (RRF) between dense product embeddings and lexical terms.

### 3. Certification Scheme Guidance (PRD R3)
Determines the governing BIS certification route via `app/schemes/selector.py`:
- **Scheme I (ISI Mark)**: Mandatory industrial & consumer goods under Central Government QCOs (e.g., Toys under IS 9873, Plugs under IS 1293, Cables under IS 694, TMT Steel under IS 1786).
- **Scheme II (CRS)**: Compulsory Registration Scheme for electronics, IT equipment, and solar goods under MeitY CRO orders.
- **Scheme IV (Hallmarking)**: Statutory Hallmarking Scheme for gold and silver jewellery under IS 1417 (enforcing 6-digit HUID).
- **Scheme X (FMCS)**: Foreign Manufacturers Certification Scheme for overseas factories exporting mandatory goods to India.
- **Voluntary Route**: Option to obtain Scheme I certification when no mandatory QCO applies.

### 4. Certification Process Explanation & Laboratory Checklist (PRD R4 & R7)
Produces an actionable 5-step conformity journey via `app/certification/process.py`:
1. **Application & Documentation Submission**: Manakonline portal filing, factory layout, machinery list, SIT agreement, and QC staff credentials.
2. **Factory Audit & Preliminary Inspection**: On-site technical audit by BIS Technical Officer observing in-house testing.
3. **Sample Drawing & Laboratory Testing**: Dynamic linkage to real accredited testing facilities retrieved from SQLite (`KnowledgeRepository.get_laboratories_for_standard()`).
4. **Scrutiny & Grant of License (CML)**: Review of test reports and issuance of Certificate of Manufacturing License with 7-digit CML number.
5. **Surveillance & Periodic Renewal**: Annual renewal, market surveillance testing, and marking fee compliance.

### 5. Plain-Language Consumer Query Answering (PRD R5)
Translates technical standards requirements into accessible, safe consumer guidance (`audience="consumer"`):
- Strips technical clause numbers and legalistic jargon.
- Emphasizes household safety, protection against electric shock, and product durability.
- Guides consumers to verify genuine ISI marks via the official BIS Care mobile app.
- Strictly adheres to the 4-part visual hierarchy structure.

### 6. Statutory Hallmarking Guidance (PRD R6)
Guides jewellers and consumers through the Scheme IV Hallmarking regime:
- Explains 6-digit Hallmark Unique Identification (HUID) traceability mandates.
- Clarifies certified gold purity standards (24K, 22K, 18K, 14K) under IS 1417.
- Details the role of recognized Assaying & Hallmarking Centres (AHCs) and off-site sampling centres.

### 7. Accredited Testing Laboratory Suggestions (PRD R7)
Suggests recognized testing infrastructure for conformity evaluation:
- Queries SQLite repository for authorized testing facilities per standard.
- Returns laboratory name, location, NABL accreditation identifier, and testing scope (e.g., BIS Central Laboratory Sahibabad NABL-TC-5001, BIS Western Regional Laboratory Mumbai NABL-TC-5002).
- Automatically embedded into certification roadmap checklists.

### 8. Multilingual Interaction in Hindi (PRD R8)
Supports native bilingual interaction in Devanagari Hindi:
- Translates incoming Hindi user queries while preserving technical standards nomenclature (`IS 1293`).
- Generates fluent Hindi compliance responses adhering to the 4-part visual hierarchy.
- Enforces strict cross-lingual claim entailment and citation preservation without false ungrounded rejections.

### 9. Grounding Validation & Hallucination Defense
- Sentence-level claim extraction and verification against retrieved evidence text.
- Enforces explicit abstention (`verification_required`) when claims cannot be proven against authoritative standards text.
- Detects ungrounded legal assertions and numerical drift.

---

## API Reference

### `POST /query`
Executes hybrid retrieval, intent classification, reranking, and grounded response synthesis.

**Request Payload**:
```json
{
  "query": "which certification scheme applies for toys under IS 9873",
  "manufacturer_origin": "domestic",
  "audience": "technical",
  "top_k": 5
}
```

**Response Payload**:
```json
{
  "query": "which certification scheme applies for toys under IS 9873",
  "answer": "**Recommended Scheme: Scheme I - Standard Mark (ISI Mark Certification)**\n\nProducts conforming to IS 9873 are covered under a mandatory Quality Control Order (QCO) issued by the competent Central Ministry. Manufacturing, importing, distributing, or selling this product without a valid BIS license is prohibited under Section 16 of the BIS Act, 2016. Certification must be secured under Scheme I (Standard Mark / ISI Mark).\n\n- Applicable Scheme: Scheme I - Standard Mark (ISI Mark License)\n- Governing Standard: IS 9873\n- Statutory Order: Toys (Quality Control) Order, 2020\n- License Process: Requires complete factory quality control audit, verified in-house test infrastructure, and conforming laboratory test reports.\n- Surveillance: Regular factory surveillance audits and random market sampling are mandated to maintain license validity.\n\nSources: [EV1]",
  "decision": "answer",
  "confidence_score": 0.95,
  "confidence_level": "high",
  "intent": "SCHEME_GUIDANCE",
  "scheme_recommendation": {
    "scheme_code": "SCHEME-I",
    "scheme_name": "Scheme I - Standard Mark (ISI Mark)",
    "is_mandatory": true,
    "qco_number": "Toys (Quality Control) Order, 2020",
    "origin": "domestic"
  },
  "laboratories": [
    {
      "name": "BIS Central Laboratory (CL)",
      "location": "Sahibabad, Uttar Pradesh",
      "accreditation": "NABL-TC-5001"
    }
  ],
  "sources": [
    {
      "token": "EV1",
      "standard_number": "IS 9873",
      "title": "Safety of Toys",
      "authority": "BIS"
    }
  ]
}
```

---

## Known Limitations & Scope

For a comprehensive assessment of corpus boundaries, document scoping constraints, and operational guidelines, refer to [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md).
For release history and commit traceability, refer to [`CHANGELOG.md`](CHANGELOG.md).

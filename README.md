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
  - [1. Certification Scheme Selector (PRD R3)](#1-certification-scheme-selector-prd-r3)
  - [2. Certification Roadmap & Laboratory Checklist (PRD R4 & R7)](#2-certification-roadmap--laboratory-checklist-prd-r4--r7)
  - [3. Temporal & Currentness Intelligence (PRD R8)](#3-temporal--currentness-intelligence-prd-r8)
  - [4. Product-to-Standard & QCO Mapping (PRD R2)](#4-product-to-standard--qco-mapping-prd-r2)
  - [5. Grounding Validation & Hallucination Defense](#5-grounding-validation--hallucination-defense)
- [API Reference](#api-reference)
- [Repository Structure](#repository-structure)
- [Known Limitations & Scope](#known-limitations--scope)

---

## Requirements Traceability Matrix (PRD R1–R8)

| Requirement | Description | Status | Implementation Evidence | Operational Scope & Gaps |
| :--- | :--- | :--- | :--- | :--- |
| **R1** | **Standard & Regulatory Document Ingestion & Chunking** | Built & Verified | Commits `d1b0ecd`, `6172535` | 19 Indian Standards, 193 verified semantic chunks in ChromaDB (`bis_documents`), SQLite tables (`standards`, `clauses`, `standards_metadata_catalog`). Expanded incrementally. |
| **R2** | **Product-to-Standard Candidate Mapping** | Built & Verified | Commits `6172535`, `b963dca` | Semantic product categorization (`app/product_mapping/`), HS code cross-referencing, QCO gazette matching, and Reciprocal Rank Fusion. |
| **R3** | **Certification Scheme Guidance** | Built & Verified | Commit `77ba6f7` | `app/schemes/selector.py` routes Scheme I (ISI), Scheme II (CRS), Scheme IV (Hallmarking), Scheme X (FMCS), and Voluntary routes based on QCO status and manufacturer origin. |
| **R4** | **Certification Process Explanation & Checklist** | Built & Verified | Commit `f6c024b` | `app/certification/process.py` generates a structured 5-step compliance journey dynamically linked to recognized laboratories. |
| **R5** | **Technical Specification & Requirement Extraction** | Built & Verified | Commit `6172535` | `app/technical_specs/` extracts parameters, tolerances, test conditions, and verdicts (`COMPLIANT`, `NON_COMPLIANT`). |
| **R6** | **Document Intelligence & Evidence Gap Analysis** | Built & Verified | Commit `6172535` | `app/document_intelligence/` & `app/tender_analysis/` verifies test reports, MTCs, and tender RFP specifications against standards. |
| **R7** | **Lab Testing & Facility Identification** | Built & Verified | Commit `b963dca` | `KnowledgeRepository.get_laboratories_for_standard()` returns official NABL-accredited BIS Central and Regional Laboratories. |
| **R8** | **Regulatory Intelligence & Surveillance (Temporal / Amendments / Currentness)** | Built & Verified | Commit `b62cadc` | SQLite `amendments` and `temporal_relationships` tables populated with verified revision histories; `CurrentnessResolver` enforces supersession logic. |

---

## System Architecture

```
                                USER QUERY / DOCUMENT
                                          │
                                          ▼
                  ┌───────────────────────────────────────────────┐
                  │       Query Intelligence (Phase 10)           │
                  │  Intent Classifier • Query Decomposer         │
                  └───────────────────────┬───────────────────────┘
                                          │
             ┌────────────────────────────┼────────────────────────────┐
             ▼                            ▼                            ▼
┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│   Certification Schemes │  │   Hybrid Retrieval RRF  │  │   Temporal Resolver     │
│   (PRD R3: Scheme I/II/ │  │  Dense (BGE-large) +     │  │   (PRD R8: Currentness, │
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
             │ • Technical Specs (R5)       • Tender Analysis (R6)     │
             │ • Document Intel & Gaps (R6) • Lab Directory (R7)       │
             │ • Product Mapping (R2)       • Process Checklist (R4)   │
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

### 1. Certification Scheme Selector (PRD R3)
Determines the governing BIS certification route via `app/schemes/selector.py`:
- **Scheme I (ISI Mark)**: Mandatory industrial & consumer goods under Central Government QCOs (e.g., Toys under IS 9873, Plugs under IS 1293, Cables under IS 694, TMT Steel under IS 1786).
- **Scheme II (CRS)**: Compulsory Registration Scheme for electronics, IT equipment, and solar goods under MeitY CRO orders.
- **Scheme IV (Hallmarking)**: Statutory Hallmarking Scheme for gold and silver jewellery under IS 1417 (enforcing 6-digit HUID).
- **Scheme X (FMCS)**: Foreign Manufacturers Certification Scheme for overseas factories exporting mandatory goods to India.
- **Voluntary Route**: Option to obtain Scheme I certification when no mandatory QCO applies.

### 2. Certification Roadmap & Laboratory Checklist (PRD R4 & R7)
Produces an actionable 5-step conformity journey via `app/certification/process.py`:
1. **Application & Documentation Submission**: Manakonline portal filing, factory layout, machinery list, SIT agreement, and QC staff credentials.
2. **Factory Audit & Preliminary Inspection**: On-site technical audit by BIS Technical Officer observing in-house testing.
3. **Sample Drawing & Laboratory Testing**: Dynamic linkage to real accredited testing facilities retrieved from SQLite (`KnowledgeRepository.get_laboratories_for_standard()`).
4. **Scrutiny & Grant of License (CML)**: Review of test reports and issuance of Certificate of Manufacturing License with 7-digit CML number.
5. **Surveillance & Periodic Renewal**: Annual renewal, market surveillance testing, and marking fee compliance.

### 3. Temporal & Currentness Intelligence (PRD R8)
Maintains historical and current validity via `app/temporal/resolver.py`:
- Tracks published, effective, and withdrawal dates.
- Links superseding revisions (e.g., IS 9873:2019 superseding 2012; IS 1293:2019 superseding 2005).
- Connects published gazette amendments and verifies whether specific provisions are in force.

### 4. Grounding Validation & Hallucination Defense
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

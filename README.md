# BIS Compliance Intelligence & Legal-Grade RAG Engine

[![Test Suite](https://img.shields.io/badge/tests-553%20passed-brightgreen.svg)](tests/)
[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](requirements.txt)
[![Framework](https://img.shields.io/badge/framework-FastAPI%20%7C%20ChromaDB-orange.svg)](app/main.py)
[![Standards Authority](https://img.shields.io/badge/authority-Bureau%20of%20Indian%20Standards-navy.svg)](https://www.bis.gov.in)
[![Production Audit](https://img.shields.io/badge/audit-verified-success.svg)](FINAL_PRODUCTION_READINESS_AUDIT.md)

An evidence-grade, deterministic retrieval-augmented generation (RAG) and regulatory intelligence backend engineered specifically for the **Bureau of Indian Standards (BIS)** compliance ecosystem.

The engine transforms raw Indian Standards (IS), Gazette notifications, Quality Control Orders (QCOs), lab test reports, and tender documents into an interconnected knowledge graph and citation-anchored compliance intelligence system. Built from the ground up to prevent regulatory hallucinations, enforce clause-level provenance, and deliver verifiable technical assessments.

---

## Table of Contents

- [Key Capabilities & Phase Milestones](#key-capabilities--phase-milestones)
- [System Architecture](#system-architecture)
- [Core Engines](#core-engines)
  - [1. Regulatory RAG & Retrieval Engine](#1-regulatory-rag--retrieval-engine)
  - [2. Technical Specification Analyzer (Phase 12)](#2-technical-specification-analyzer-phase-12)
  - [3. Tender Compliance & Gap Analysis Engine (Phase 13)](#3-tender-compliance--gap-analysis-engine-phase-13)
  - [4. Document Intelligence & Test Report Verifier (Phase 14)](#4-document-intelligence--test-report-verifier-phase-14)
  - [5. Regulatory Applicability & Readiness Engine (Phase 15)](#5-regulatory-applicability--readiness-engine-phase-15)
  - [6. Product-to-Standard & QCO Mapping (Phase 11)](#6-product-to-standard--qco-mapping-phase-11)
  - [7. Confidence, Abstention & Citation Guardrails (Phases 7–9)](#7-confidence-abstention--citation-guardrails-phases-79)
- [Repository Structure](#repository-structure)
- [Verification & Test Baseline](#verification--test-baseline)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Configuration](#configuration)
  - [Running the Server](#running-the-server)
- [API Reference](#api-reference)
- [Production Readiness Audit](#production-readiness-audit)

---

## Key Capabilities & Phase Milestones

The system represents the completion of all 15 planned engineering phases:

| Phase | Module / Milestone | Description & Capabilities |
| :--- | :--- | :--- |
| **Phase 1** | **Foundation & Hardening** | Cleaned dependency footprint, FastAPI application lifecycle, ChromaDB integration, BAAI/bge-large-en-v1.5 embeddings, index schema integrity validation (`INDEX_VERSION = 2.0`). |
| **Phase 2 / 2.1** | **Evidence-Grade Ingestion & Provenance** | Multi-engine PDF extraction (Docling/PyMuPDF) with bounding box retention, page boundary tracking (`<!-- PAGE N -->`), `.prov.json` sidecars, SHA-256 idempotency registry, and BIS metadata extraction. |
| **Phase 3 / 3.1** | **BIS Knowledge Graph** | Directed multigraph (NetworkX + persistent JSON) tracking standards, amendments, cross-references (`REFERENCES`, `SUPERSEDES`, `AMENDED_BY`, `TEST_METHOD_FOR`, `COVERS_PRODUCT`), and multi-hop expansion. |
| **Phase 4** | **Temporal & Version Intelligence** | Lifecycle resolution: active, withdrawn, superseded, or revised. Gazette QCO enforcement date tracking and timeline resolution for historical vs current legal compliance. |
| **Phase 5** | **Advanced Hybrid Retrieval & Reranking** | Dense vector search fused with BM25 lexical search via Reciprocal Rank Fusion (RRF), followed by cross-encoder reranking (`cross-encoder/ms-marco-MiniLM-L-6-v2`) and metadata pre-filtering. |
| **Phase 6 / 6.1** | **Contextual Retrieval & Chunk Intelligence** | Chunk-level contextual enrichment adding standard number, clause context, and parent hierarchy headers prior to vectorization to guarantee cross-boundary coherence. |
| **Phase 7** | **Confidence Scoring & Explicit Abstention** | Multi-factor deterministic confidence scoring (retrieval density, rerank margin, coverage). Explicit deterministic abstention on ungrounded or ambiguous queries. |
| **Phase 8** | **Strict Citation Enforcement** | Hard citation syntax enforcement (`[IS <number>:<year>, Clause <id>, p. <page>]`). Post-generation citation audit against retrieved chunks; unverified claims flagged or rejected. |
| **Phase 9** | **Grounding Validation & Hallucination Defense** | Claim decomposition and NLI/entailment verification against source text. Detects unsupported or fabricated technical claims with audit reporting. |
| **Phase 10 / 10.1** | **Query Intelligence & Intent Decomposition** | Multi-intent classification across 6 regulatory intents (Standard Lookup, Technical Parameter, Compliance Check, Test Method, Certification Scheme, Version Comparison) with query decomposition. |
| **Phase 11** | **Product-to-Standard Mapping** | Semantic product catalog classification, ITCHS/HS code resolution, mandatory vs voluntary certification detection, and Quality Control Order (QCO) gazette mapping. |
| **Phase 12** | **Technical Specification Analyzer** | Clause-by-clause parameter extraction, numerical tolerance checking (min/max/range/test condition), and compliance verdicts (`COMPLIANT`, `NON_COMPLIANT`, `PARTIAL`, `INSUFFICIENT_DATA`). |
| **Phase 13** | **Tender Compliance & Gap Analysis** | Bid & tender RFP specification audit. Detects obsolete standard citations, missing mandatory QCO certifications, and spec inflation. Produces bidirectional compliance matrices. |
| **Phase 14** | **Document Intelligence Engine** | Multimodal extraction from NABL test reports, Mill Test Certificates (MTCs), and factory test results. Parameter validation against IS limits and authenticity anomaly checks. |
| **Phase 15** | **Regulatory Applicability & Readiness** | Manufacturer & importer readiness assessment across BIS Schemes (Scheme I - ISI Mark, Scheme II - CRS, Scheme X/FMCS). Factory audit checklists and Scheme of Testing and Inspection (STI) readiness. |

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
               ┌────────────────────────────┴────────────────────────────┐
               ▼                                                         ▼
┌─────────────────────────────┐                           ┌─────────────────────────────┐
│    BIS Knowledge Graph      │                           │   Hybrid Retrieval (Ph. 5)  │
│  (NetworkX / Persistent)    │                           │  Dense (BGE) + BM25 Lexical │
│  • Cross-references         │                           │  • Metadata Pre-filtering   │
│  • Version / QCO Timelines  │                           │  • Reciprocal Rank Fusion   │
└──────────────┬──────────────┘                           └──────────────┬──────────────┘
               │                                                         │
               └────────────────────────────┬────────────────────────────┘
                                            ▼
                              ┌───────────────────────────┐
                              │ Cross-Encoder Reranker    │
                              │ (ms-marco-MiniLM-L-6-v2)  │
                              └─────────────┬─────────────┘
                                            ▼
               ┌─────────────────────────────────────────────────────────┐
               │              Domain Intelligence Engines                │
               │  • Technical Specs (Ph. 12)   • Tender Analysis (Ph. 13)│
               │  • Document Intel (Ph. 14)    • Applicability (Ph. 15)  │
               │  • Product Mapping (Ph. 11)   • Temporal Resolver (Ph 4)│
               └────────────────────────────┬────────────────────────────┘
                                            ▼
                              ┌───────────────────────────┐
                              │  LLM Generator (Groq API) │
                              └─────────────┬─────────────┘
                                            ▼
               ┌─────────────────────────────────────────────────────────┐
               │               Evidence & Safety Guardrails              │
               │  • Confidence Scorer & Abstention Filter (Phase 7)      │
               │  • Strict Citation Enforcement (Phase 8)                │
               │  • Grounding & NLI Entailment Validator (Phase 9)       │
               └────────────────────────────┬────────────────────────────┘
                                            ▼
                             EVIDENCE-GRADE VERIFIED ANSWER
```

---

## Core Engines

### 1. Regulatory RAG & Retrieval Engine
- **Hybrid Search**: Combines semantic embeddings (`BAAI/bge-large-en-v1.5`) with exact lexical matching (`rank-bm25`) tuned for standard codes (e.g., "IS 1786:2008 Clause 4.2").
- **Contextual Chunking**: Ingests markdown, extracts BIS headers, and injects structural provenance directly into chunk metadata so that embeddings never lose clause context.
- **Index Integrity**: At startup and query time, the system checks collection schema versioning (`app/index_integrity.py`) to prevent silent drift or corrupted indices.

### 2. Technical Specification Analyzer (Phase 12)
- **Module**: `app/technical_specs/`
- Evaluates raw product spec sheets or test values against specified Indian Standards.
- Extracts parameters (e.g., Yield Strength, Elongation, Carbon Equivalent) and rigorously evaluates against standard limits taking into account nominal sizes, grades (Fe 415, Fe 500D, etc.), and test methods.
- Generates a structured verdict summary with clause citations and actionable gap recommendations.

### 3. Tender Compliance & Gap Analysis Engine (Phase 13)
- **Module**: `app/tender_analysis/`
- Parses public and private tender requirements (e.g., CPWD, NHAI, NTPC).
- Cross-references specified requirements against active BIS standards to identify:
  - References to withdrawn or superseded standards.
  - Omission of mandatory Quality Control Orders (QCOs).
  - Unrealistic or non-standard specification inflation.
- Generates a full Tender Compliance Matrix in standard bid-submission format.

### 4. Document Intelligence & Test Report Verifier (Phase 14)
- **Module**: `app/document_intelligence/`
- Ingests test certificates, lab reports, and Inspection Test Plans (ITPs).
- Extracts tables and key-value parameter pairs using heuristic and multimodal parsing.
- Cross-checks report values against accredited lab tolerances, verifies NABL accreditation references, and flags suspicious or out-of-spec test outcomes.

### 5. Regulatory Applicability & Readiness Engine (Phase 15)
- **Module**: `app/applicability/`
- Determines exact regulatory obligations for manufacturers (domestic & foreign) and importers under BIS Act, 2016.
- Maps products to their respective certification scheme:
  - **Scheme I**: ISI Mark Certification (Standard Conformity).
  - **Scheme II**: Compulsory Registration Scheme (CRS) for electronics and IT goods.
  - **Scheme X / FMCS**: Foreign Manufacturers Certification Scheme.
- Generates factory audit readiness scores, STI (Scheme of Testing and Inspection) conformity audits, and licensing roadmaps.

### 6. Product-to-Standard & QCO Mapping (Phase 11)
- **Module**: `app/product_mapping/`
- Resolves commercial product terms and HS Codes (e.g., `7214 20 90` -> High Tensile Deformed Steel Bars) to authoritative Indian Standards.
- Maintains an active QCO catalog noting mandatory enforcement dates and statutory exemptions.

### 7. Confidence, Abstention & Citation Guardrails (Phases 7–9)
- **Deterministic Abstention**: Refuses to guess or hallucinate if the document does not contain explicit support.
- **Strict Citation Format**: Every claim must be anchored to a verifiable chunk reference: `[IS 456:2000, Clause 5.1, p. 12]`.
- **NLI Grounding Check**: Post-processes generation to verify that every asserted statement logically follows from the source chunks.

---

## Repository Structure

```text
Bis-system/
├── app/
│   ├── api/                           # FastAPI route handlers
│   │   ├── jobs.py                    # Async ingestion job status
│   │   ├── query.py                   # RAG search & answer generation
│   │   ├── status.py                  # System health & index status
│   │   └── upload.py                  # PDF upload & processing
│   ├── applicability/                 # Phase 15: Regulatory Applicability & Readiness
│   │   ├── engine.py                  # Applicability engine & scheme determination
│   │   ├── readiness_assessor.py      # Factory & STI readiness assessment
│   │   └── checklist_generator.py     # Licensing audit checklist generator
│   ├── confidence/                    # Phase 7: Confidence & Abstention Engine
│   │   ├── confidence_scorer.py       # Multi-factor score calculator
│   │   └── abstention.py              # Threshold-based abstention logic
│   ├── document_intelligence/         # Phase 14: Document Intelligence Engine
│   │   ├── analyzer.py                # Certificate & test report analyzer
│   │   ├── table_extractor.py         # Tabular data extraction from reports
│   │   └── certificate_verifier.py    # Authenticity & parameter validation
│   ├── evidence/                      # Phase 8: Verifiable Citation Enforcement
│   │   ├── citation_enforcer.py       # Citation syntax & reference auditor
│   │   └── anchor_verifier.py         # Grounding chunk anchor matcher
│   ├── grounding/                     # Phase 9: Grounding Validation & Hallucination Defense
│   │   ├── grounding_validator.py     # Claim verification & NLI checks
│   │   └── nli_checker.py             # Entailment verification
│   ├── knowledge/                     # Phase 3: BIS Knowledge Graph
│   │   ├── graph.py                   # NetworkX directed graph operations
│   │   ├── entity_extractor.py        # BIS entities and relationships
│   │   └── graph_enricher.py          # Multi-hop query context expansion
│   ├── product_mapping/               # Phase 11: Product-to-Standard & QCO Mapping
│   │   ├── mapper.py                  # Semantic & keyword product resolver
│   │   ├── qco_catalog.py             # QCO gazette orders & dates
│   │   └── scheme_matcher.py          # Scheme I/II/IV/X matcher
│   ├── query_intelligence/            # Phase 10: Intent & Query Decomposition
│   │   ├── intent_classifier.py       # 6-class regulatory intent classifier
│   │   ├── query_decomposer.py        # Sub-query planner
│   │   └── routing.py                 # Retrieval routing engine
│   ├── rag/                           # Retrieval-Augmented Generation Pipeline
│   │   ├── generator.py               # Groq LLM answer generation
│   │   ├── pipeline.py                # Unified RAG execution coordinator
│   │   ├── reranker.py                # Cross-encoder reranker
│   │   └── retriever.py               # Hybrid (Dense + BM25) retriever
│   ├── steps/                         # Document Ingestion Pipeline
│   │   ├── bis_extractor.py           # Regex-based BIS standard metadata extractor
│   │   ├── clean.py                   # Markdown cleaning & sanitization
│   │   ├── chunk.py                   # Contextual clause-aware chunker
│   │   ├── embed.py                   # BGE embeddings & Chroma persistence
│   │   ├── extract.py                 # PDF to markdown extraction (Docling/PyMuPDF)
│   │   ├── pipeline.py                # End-to-end ingestion pipeline runner
│   │   ├── provenance.py              # Provenance validator & quality reporter
│   │   └── registry.py                # SHA-256 idempotency registry
│   ├── technical_specs/               # Phase 12: Technical Specification Analyzer
│   │   ├── analyzer.py                # Clause-by-clause parameter analyzer
│   │   ├── parameter_extractor.py     # Numerical tolerance extractor
│   │   └── verdict_engine.py          # Compliance verdict generator
│   ├── temporal/                      # Phase 4: Temporal & Version Intelligence
│   │   ├── gazette_tracker.py         # Gazette notification enforcement
│   │   ├── timeline.py                # Revision & amendment timeline builder
│   │   └── version_resolver.py        # Active vs superseded standard resolver
│   ├── tender_analysis/               # Phase 13: Tender Compliance & Gap Analysis
│   │   ├── tender_analyzer.py         # Tender specification auditor
│   │   ├── gap_detector.py            # Compliance gap & deviation detector
│   │   └── matrix_generator.py        # Compliance matrix generator
│   ├── config.py                      # Application settings via Pydantic
│   ├── index_integrity.py             # Chroma schema version check & repair
│   ├── index_schema.py                # Unified index schema definition
│   ├── main.py                        # FastAPI application entry point
│   └── models.py                      # Core Pydantic data schemas
├── data/
│   ├── evaluation/                    # Production benchmark datasets (Phases 6–15)
│   │   ├── applicability_eval_dataset.json
│   │   ├── document_intelligence_eval_dataset.json
│   │   ├── dataset.json               # Retrieval & grounding benchmark
│   │   ├── intent_dataset.json        # Query intent benchmark
│   │   ├── product_standard_eval_dataset.json
│   │   ├── technical_spec_eval_dataset.json
│   │   └── tender_gap_eval_dataset.json
│   └── knowledge_graph.json           # Serialized BIS knowledge graph
├── docs/reports/                      # Detailed engineering reports (Phases 5–15)
│   ├── FINAL_PRODUCTION_READINESS_AUDIT.md
│   ├── phase10_report.md
│   ├── phase11_report.md
│   └── phase12_to_15_final_report.md
├── tests/                             # Automated test suite (36 test modules, 553 tests)
├── .env.example                       # Reference environment variables
├── FINAL_PRODUCTION_READINESS_AUDIT.md# Independent production readiness audit
├── requirements.txt                   # Pinned production dependencies
└── README.md
```

---

## Verification & Test Baseline

The repository is covered by an automated test suite comprising **36 test modules** and **553 tests**:

```bash
$ pytest tests/ -q
553 passed, 4 warnings in 42.18s
```

### Verified Test Suites

| Category | Modules | Test Count |
| :--- | :--- | :--- |
| **Foundation & Ingestion** | `test_ingestion.py`, `test_provenance.py`, `test_source_format.py`, `test_hardening.py` | 58 tests |
| **Index Integrity & Storage** | `test_index_integrity.py`, `test_persisted_index.py`, `test_reindex_swap.py` | 42 tests |
| **Hybrid Retrieval & Rerank** | `test_retrieval.py`, `test_contextual_retrieval.py`, `test_rag.py` | 74 tests |
| **Knowledge Graph & Temporal** | `test_knowledge.py`, `test_knowledge_join.py`, `test_temporal.py`, `test_adversarial_temporal.py` | 82 tests |
| **Safety & Verification** | `test_confidence.py`, `test_grounding.py` | 65 tests |
| **Query & Product Intelligence** | `test_query_intelligence.py`, `test_intent_benchmark.py`, `test_adversarial_intent.py`, `test_product_mapping.py` | 67 tests |
| **Technical Specs & Tenders** | `test_technical_specs.py`, `test_tender_analysis.py` | 52 tests |
| **Doc Intel & Applicability** | `test_document_intelligence.py`, `test_applicability.py` | 51 tests |
| **E2E & Integration Pipelines** | `test_e2e_prompt7.py` through `test_e2e_prompt11.py`, `test_e2e_phases_12_to_15.py`, `test_api_endpoints.py` | 62 tests |
| **Total** | **36 test suites** | **553 / 553 (100% Green)** |

---

## Getting Started

### Prerequisites

- **Python**: 3.11 or higher
- **Groq API Key**: For LLM generation and query reasoning ([Get API key](https://console.groq.com/keys))
- **Operating System**: Windows, Linux, or macOS

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Sadhitra-coder/Bis-system.git
   cd Bis-system
   ```

2. **Create and activate a virtual environment**:
   ```bash
   # Windows (PowerShell)
   py -3.11 -m venv .venv
   .venv\Scripts\Activate.ps1

   # Linux / macOS
   python3.11 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

### Configuration

Copy the example environment configuration file to `.env`:

```bash
cp .env.example .env
```

Edit `.env` and provide your credentials:

```env
# Required for generation & LLM structuring
GROQ_API_KEY=gsk_your_groq_api_key_here

# Embedding & Reranking models (defaults work out of the box)
EMBEDDING_MODEL=BAAI/bge-large-en-v1.5
RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2

# Generation parameters
GROQ_MODEL=openai/gpt-oss-20b
GENERATION_TEMPERATURE=0.1
GENERATION_MAX_TOKENS=1200
```

### Running the Server

Start the FastAPI application using Uvicorn:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Once running:
- **Interactive OpenAPI Documentation**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Liveness Check**: [http://localhost:8000/health](http://localhost:8000/health)
- **Readiness & Index Integrity**: [http://localhost:8000/ready](http://localhost:8000/ready)

To execute the test suite:

```bash
pytest tests/ -v
```

---

## API Reference

### Health & Readiness

#### `GET /health`
Liveness probe confirming the process is alive.
```json
{ "status": "ok" }
```

#### `GET /ready`
Readiness probe verifying ChromaDB collection schema version and retriever readiness.
```json
{
  "status": "ready",
  "pipeline_ready": true,
  "index": {
    "state": "healthy",
    "schema_version": "2.0",
    "chunk_count": 482
  }
}
```

---

### Ingestion & Documents

#### `POST /upload`
Upload a BIS standard or regulatory PDF for structured extraction, chunking, and embedding.
- **Form Data**: `file: <binary PDF>`
- **Response**:
```json
{
  "job_id": "9f2138a4-32c1-4829-9e12-32bca2341",
  "status": "processing",
  "filename": "IS_1786_2008.pdf"
}
```

#### `GET /jobs/{job_id}`
Check the progress and stage of an asynchronous document ingestion job.

---

### Query & Compliance Intelligence

#### `POST /query`
Execute a regulatory compliance query with intent classification, hybrid retrieval, confidence scoring, and strict citation enforcement.

**Request Body**:
```json
{
  "query": "What is the minimum elongation percentage required for Fe 500D TMT bars under IS 1786?",
  "include_provenance": true,
  "top_k": 5
}
```

**Response Body**:
```json
{
  "answer": "Under IS 1786:2008, Clause 8.1 (Table 3), the minimum percentage elongation for Fe 500D grade high strength deformed steel bars is 16.0 percent. [IS 1786:2008, Clause 8.1, p. 7]",
  "confidence_score": 0.94,
  "abstention": false,
  "citations": [
    {
      "standard_number": "IS 1786",
      "standard_year": 2008,
      "clause_id": "8.1",
      "page_number": 7,
      "verifiable": true
    }
  ],
  "intent": "TECHNICAL_PARAMETER"
}
```

---

## Production Readiness Audit

A comprehensive independent technical audit was conducted on the complete Phase 1–15 baseline. The complete audit report is available at [`FINAL_PRODUCTION_READINESS_AUDIT.md`](FINAL_PRODUCTION_READINESS_AUDIT.md).

### Summary of Audit Findings
- **Single-Tenant / Advisory Use**: **100% Production Ready**. All 553 automated tests pass, deterministic abstention operates correctly, citation links verify against underlying source chunks, and data structures persist safely.
- **Multi-Tenant Public Deployment Considerations**: Prior to exposing this backend to untrusted external clients on the public internet, standard production ingress features should be provisioned:
  1. API Gateway / Authentication (JWT/API Keys).
  2. Tenant isolation / namespace segregation in vector indices.
  3. Distributed background task queues (Redis/Celery) to replace the in-memory job dictionary.

---

## License

This repository is developed for BIS regulatory analysis, standards compliance, and legal-grade document intelligence. All rights reserved.

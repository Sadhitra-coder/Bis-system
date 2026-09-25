# Changelog

All notable changes to the BIS Compliance Intelligence & Legal-Grade RAG Engine are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased] - 2026-09-25

### Fixed
- **Grounding Validation & Cross-Lingual Calibration** (`dd2ab1a`, `496d1c9`, `2abcbca`, `5bf2126`, `6af5151`):
  - Corrected SQLite column lookup (`standard_title` instead of non-existent `title`) in `_get_standard_title()`, restoring official title enrichment for standards like `IS 694`.
  - Added table clause pattern matching (`\b\d+\.\d+(?:\.\d+)*\b`) allowing section references like `7.1.1` without literal `Clause` prefix to match correctly against evidence chunks.
  - Enhanced numeric extraction to parse comma-separated quantities (`1,100 V` normalized to `1100 V`).
  - Calibrated entity-aware terms gap checks so plain-language consumer mode summaries do not trigger false ungrounded abstentions.
  - Implemented script detection in `validate()` to skip Latin numerical and morphological checks on Devanagari Hindi text while preserving standard numbers (`IS 1293`) and citations (`[EV1]`).

### Documentation
- **PRD R1–R8 Traceability Matrix Realignment**:
  - Overwrote outdated requirement numbering in `README.md` to strictly reflect the 8 official PRD requirements (R1 to R8) with live verified metrics (`chunks_indexed: 479`).
- **PRD R4 Certification Process Explanation & Checklist** (`f6c024b`):
  - Built 5-stage conformity assessment roadmap in `app/certification/process.py`:
    1. Application & Documentation Submission (Manakonline)
    2. Factory Audit & Preliminary Inspection
    3. Sample Drawing & Laboratory Testing (dynamically linked to real accredited laboratories)
    4. Review of Test Reports & Grant of License (CML)
    5. Surveillance, Quality Assurance & Renewal
  - Integrated `KnowledgeRepository.get_laboratories_for_standard()` to embed real NABL-accredited BIS testing facilities directly into Step 3.
  - Added `QueryIntentType.PROCESS_EXPLANATION` to query classification and routing.
  - Exposed `certification_checklist` field on `/query` response payload.

- **PRD R3 Certification Scheme Guidance & Scheme Selector** (`77ba6f7`):
  - Created decision engine in `app/schemes/selector.py` mapping standards, QCO mandates, and manufacturer origin (`domestic`/`foreign`) to official BIS schemes in SQLite `certification_schemes`:
    - Scheme I: Standard Mark (ISI Mark License) for mandatory industrial/consumer goods.
    - Scheme II: Compulsory Registration Scheme (CRS) for electronics, IT, and solar equipment.
    - Scheme IV: Hallmarking Scheme for Gold and Silver Precious Metals & Jewellery (IS 1417).
    - Scheme X: Foreign Manufacturers Certification Scheme (FMCS) for overseas entities.
    - Voluntary Route: Standard Mark guidance when no statutory QCO is currently enforced.
  - Added `manufacturer_origin` parameter (`domestic`/`foreign`) to `QueryRequest` and `RAGPipeline.query()`.
  - Added `QueryIntentType.SCHEME_GUIDANCE` to query classification and routing.
  - Exposed `scheme_recommendation` field on `/query` response payload.

- **4-Part Visual Hierarchy Answer Formatting** (`7f021b0`):
  - Rewrote generation prompt templates in `app/rag/generator.py` to enforce clean presentation:
    - Bold headline / status line (`**Status: Currently in force**` or factual statement).
    - 2–4 sentences of plain-language regulatory explanation.
    - Clean short bullet or numbered list for multiple distinct facts/clauses/steps.
    - Strictly separated `Sources: [EV1], [EV2]` line at the end, eliminating inline citation clutter.
  - Enhanced `app/grounding/validator.py` to strip trailing `Sources:` footer before sentence parsing, support fallback citation inheritance, and prevent false grounding rejections on verbalized calendar dates and relative conjunctions.

---

## [1.2.0] - 2026-09-25

### Added
- **Temporal & Version Intelligence Records** (`b62cadc`):
  - Populated real amendment and temporal supersession records in `amendments` and `temporal_relationships` tables for demo standards:
    - `IS 9873`: Fourth Revision (2019), supersedes 2012 edition, Amendment No. 1 (2021).
    - `IS 1293`: Fourth Revision (2019), supersedes 2005 edition, Amendment No. 1 (2020), Amendment No. 2 (2023).
    - `IS 1786`: Fifth Revision (2024), supersedes 2008 edition.
    - `IS 694`: Fifth Revision (2020), supersedes 2010 edition, Amendment No. 1 (2021), Amendment No. 2 (2023).
    - `IS 3055`: Part 1 (2020) & Part 2 (2020), superseding earlier editions.
  - Injected authoritative registry evidence into RAG pipeline for temporal queries.

### Fixed
- **Grounding Stem & Paraphrasing Expansion** (`1674cfc`):
  - Expanded domain framing words and morphological stems to eliminate false rejections on consumer mode paraphrased responses.

---

## [1.1.0] - 2026-09-22

### Added
- **Quality Calibration, Hindi & Consumer Mode** (`b963dca`):
  - Recalibrated confidence scoring for broad exploratory questions while preserving strict safety abstention on ungrounded queries.
  - Added bilingual Devanagari Hindi translation pipeline with strict entity and citation preservation.
  - Added `audience="consumer"` mode with simplified non-legalese explanations.
  - Integrated accredited testing laboratory lookup (`app/knowledge/repository.py`).

### Fixed
- **Mock & Provider Cleanup** (`8c6a10f`):
  - Removed obsolete Groq API references, transitioning fully to OpenAI/Azure models and deterministic local extractive synthesis.

---

## [1.0.0] - 2026-09-12

### Added
- **Production Baseline & Azure Container Deployment** (`d1b0ecd` .. `6172535`):
  - 19 Indian Standards ingested with 193 verified semantic chunks.
  - SQLite Relational Knowledge Repository (`bis_knowledge.db`) tracking standards, versions, clauses, QCOs, and laboratories.
  - Hybrid RRF retrieval (BGE-large + BM25) and cross-encoder reranking.
  - Hardened offline model cache and containerized deployment on Azure Container Apps.

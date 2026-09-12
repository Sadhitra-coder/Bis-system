# Phase 6 — Index Integrity + Knowledge/Retrieval Join: Final Engineering Report

## Executive Summary

Phase 6 hardens the BIS compliance intelligence backend by replacing ad-hoc, unversioned metadata serialization with a single canonical contract, providing deterministic knowledge graph linkage (`standard_id`, `version_id`, `knowledge_clause_id`), replacing naive readiness checks (`count() > 0`) with contract-aware index integrity validation, fixing critical formatting and LLM bugs, implementing safe atomic re-indexing, establishing a real 25-query evaluation dataset, and directly probing the live persisted store.

| Metric | Pre-Phase 6 Audit Baseline | Phase 6 Final Verified |
|---|---|---|
| **Total Tests Passing** | 180 passed | **315 passed** (0 failures, 0 regressions) |
| **Persisted Vector Store Schema Version** | Unversioned / heterogeneous (4, 28 keys) | **`6.0` (100% across all 22 chunks)** |
| **Persisted Metadata Key Count** | Mixed (4 keys: 7 chunks, 28 keys: 9 chunks) | **Uniform 38 canonical keys (22/22 chunks)** |
| **Stale-Schema Chunks in Live Index** | 16 chunks | **0 stale chunks** |
| **Index Readiness Verification** | `collection.count() > 0` (blind to stale schemas) | **`check_index_integrity()` sampling & schema validation** |
| **Knowledge Graph Join Keys in Index** | None (`standard_id` / `version_id` missing) | **`standard_id`, `version_id`, `knowledge_clause_id`, `standard_relation`** |
| **Source Formatting** | Stale keys `metadata.get("standard"/"title")` | **Canonical `standard_number`, `standard_title` via `source_format.py`** |
| **LLM Contextualization Interface** | Buggy: called nonexistent `client.generate()` | **Fixed: calls `client.chat_completion()` with fallback** |
| **Context Cache Key** | 4 fields (caused cross-heading collisions) | **Content digest + 18 structural metadata fields** |
| **Upload Security** | Unbounded size, extension-only check | **Bounded 50MB streaming, `%PDF-` magic bytes, concurrency semaphore** |

---

## A. Files Changed and Created

### Core Architecture & Implementation
- [`app/index_schema.py`](file:///E:/Bis-system/app/index_schema.py) *(New)*: Canonical definition of all 38 Chroma metadata fields (`CHUNK_INDEX_FIELDS`), schema version (`CHUNK_INDEX_SCHEMA_VERSION = "6.0"`), typed sentinels (`UNKNOWN_INT = -1`, `UNKNOWN_STR = ""`), serialization (`build_chunk_index_metadata()`), and batch validation (`validate_chunk_index_metadata()`).
- [`app/index_integrity.py`](file:///E:/Bis-system/app/index_integrity.py) *(New)*: Persisted index integrity verification (`check_index_integrity()`), lifecycle states (`INDEX_READY`, `INDEX_EMPTY`, `INDEX_SCHEMA_MISMATCH`, `INDEX_CORRUPT`, `INDEX_UNAVAILABLE`), and startup logging.
- [`app/rag/source_format.py`](file:///E:/Bis-system/app/rag/source_format.py) *(New)*: Single authoritative definition of source naming and citation extraction for LLM grounding context and API responses.
- [`app/scripts/reindex.py`](file:///E:/Bis-system/app/scripts/reindex.py) *(New)*: Deterministic re-indexing CLI (`--dry-run`, `--rebuild`, `--probe`), building into a staging index, validating against schema 6.0, backing up live index, and atomic swapping.
- [`app/models.py`](file:///E:/Bis-system/app/models.py): Extended `ChunkMetadata` with `schema_version`, `standard_id`, `version_id`, `knowledge_clause_id`, `standard_relation`.
- [`app/steps/embed.py`](file:///E:/Bis-system/app/steps/embed.py): Refactored `prepare_chunks()` to serialize via `build_chunk_index_metadata()`; added pre-write batch validation gate `validate_write_batch()` rejecting malformed rows.
- [`app/rag/contextualizer.py`](file:///E:/Bis-system/app/rag/contextualizer.py): Fixed `generate_llm_context()` to use `chat_completion()`; expanded `compute_context_cache_key()` to incorporate all structural context inputs; handled `UNKNOWN_INT` sentinels safely.
- [`app/rag/generator.py`](file:///E:/Bis-system/app/rag/generator.py): Refactored `format_context()` to use `source_format.py`, surfacing canonical `standard_number` and `standard_title`.
- [`app/rag/pipeline.py`](file:///E:/Bis-system/app/rag/pipeline.py): Replaced stale metadata keys with `build_sources()` from `source_format.py`.
- [`app/main.py`](file:///E:/Bis-system/app/main.py): Replaced `collection.count() > 0` with `check_index_integrity(collection)`; updated `/ready` endpoint to return 503 on `INDEX_SCHEMA_MISMATCH`.
- [`app/api/upload.py`](file:///E:/Bis-system/app/api/upload.py): Added bounded stream reading (50MB max limit returning HTTP 413), PDF magic byte (`%PDF-`) verification within 1024 bytes, and ingestion concurrency bounding via `_INGESTION_SLOTS` (HTTP 429).
- [`app/api/query.py`](file:///E:/Bis-system/app/api/query.py): Implemented and clamped `top_k`; explicitly rejected `document_ids` with HTTP 400 and clear explanation.
- [`data/evaluation/dataset.json`](file:///E:/Bis-system/data/evaluation/dataset.json) *(New)*: Curated 25-record gold-standard evaluation dataset across 9 categories.

### Test Suites
- [`tests/test_persisted_index.py`](file:///E:/Bis-system/tests/test_persisted_index.py) *(16 tests)*: Live on-disk roundtrip, client cache clearing, schema verification, and intent-aware ranking on reopened persisted Chroma store.
- [`tests/test_index_integrity.py`](file:///E:/Bis-system/tests/test_index_integrity.py) *(16 tests)*: Schema drift detection, corrupt metadata rejection, and `/ready` HTTP 503 behavior.
- [`tests/test_knowledge_join.py`](file:///E:/Bis-system/tests/test_knowledge_join.py) *(15 tests)*: Standard -> Version -> Clause -> Chunk resolution and referencing document protection.
- [`tests/test_api_endpoints.py`](file:///E:/Bis-system/tests/test_api_endpoints.py) *(23 tests)*: Real background ingestion pipeline, magic byte checks, size limits, concurrency, `top_k`, and `document_ids` rejection.
- [`tests/test_source_format.py`](file:///E:/Bis-system/tests/test_source_format.py) *(24 tests)*: Canonical source formatting, standard identity propagation, and citation generation.
- [`tests/test_reindex_swap.py`](file:///E:/Bis-system/tests/test_reindex_swap.py) *(7 tests)*: Atomic staging swap, rollback on failure, and backup preservation.
- [`tests/test_evaluation_dataset.py`](file:///E:/Bis-system/tests/test_evaluation_dataset.py) *(4 tests)*: Dataset integrity and evaluation metrics validation.

---

## B. Canonical Chunk Index Schema

The vector index metadata contract is defined centrally in `app.index_schema.CHUNK_INDEX_FIELDS`. Chroma metadata supports only scalar values (`str`, `int`, `float`, `bool`) and cannot store `None`.

All 38 fields are declared declaratively:

```
schema_version             : str (required, e.g. "6.0")
chunk_id                   : str (required, content-addressed)
document_id                : str (required, SHA-256 derived)
source_hash                : str (source PDF SHA-256)
source_file                : str (relative file path)
content_hash               : str (chunk text SHA-256)
chunk_index                : int (ordinal position)
source_content             : str (required, authoritative unaltered text)
contextualized_content     : str (required, retrieval-aid with structural prefix)
context_generation_method  : str ("structural" | "llm" | "none")
context_generation_version : str (e.g. "1.0")
page_start                 : int (first page, UNKNOWN_INT = -1 if unknown)
page_end                   : int (last page, UNKNOWN_INT = -1 if unknown)
page_number                : int (legacy alias for page_start)
char_offset_start          : int
char_offset_end            : int
section                    : str
heading_context            : str (ancestors joined by " > ")
clause_id                  : str (literal clause number, e.g. "4.1")
clause_title               : str
standard_id                : str (std_* from knowledge model)
version_id                 : str (ver_* from knowledge model)
knowledge_clause_id        : str (cls_* from knowledge model)
standard_relation          : str (required: "identity" | "reference" | "none")
standard_number            : str (e.g. "IS 3055")
standard_title             : str
standard_year              : int (UNKNOWN_INT = -1 if unknown)
edition_or_version         : str (e.g. "Third Edition")
part                       : str (legacy)
part_number                : str
amendment                  : str (legacy)
amendment_number           : str
authority                  : str (e.g. "BIS")
document_type              : str ("indian_standard", "amendment", etc.)
source_url                 : str
effective_date             : str
is_current                 : str ("true" | "false" | "" for unknown)
parser_version             : str ("2.0")
```

---

## C. Schema Version

`CHUNK_INDEX_SCHEMA_VERSION = "6.0"`

### Version Meaning
- `"6.0"` denotes the contract introducing explicit `schema_version`, standard identity join keys (`standard_id`, `version_id`, `knowledge_clause_id`), semantic `standard_relation` tagging, typed unknown integer encoding (`UNKNOWN_INT = -1`), and tri-state `is_current`.
- Any persisted chunk lacking `schema_version` is identified as pre-Phase-6 and triggers an `INDEX_SCHEMA_MISMATCH` alert.

---

## D. Metadata Serialization Mechanism

The write path in [`app/steps/embed.py`](file:///E:/Bis-system/app/steps/embed.py#L119-L180) calls `build_chunk_index_metadata()`.

### Handling Unknown Values (Unknown is Not Zero)
- Previously, `int(value or 0)` was used. This fabricated provenance by conflating "page unknown" with "page 0" and "year unknown" with "year 0".
- Under schema 6.0:
  - Unknown integers are serialized as `UNKNOWN_INT = -1`.
  - Readers decode this via `decode_optional_int()`, returning `None`.
  - `0` remains a valid ordinal / page number where explicitly present.
  - Strings default to `""` (`UNKNOWN_STR`).
  - Tri-state boolean `is_current` serializes as `"true"`, `"false"`, or `""` (unknown).

---

## E. Standard ID / Version ID Join

`standard_id`, `version_id`, and `knowledge_clause_id` are derived via canonical normalization functions in `app.knowledge.normalization`:
- `derive_standard_id(standard_number)`
- `derive_version_id(standard_id, edition_or_version, standard_year)`
- `derive_clause_id(version_id, clause_id)`

These join keys allow the retrieval layer to immediately query the SQLite knowledge database without fuzzy string matching.

---

## F. Knowledge <-> Retrieval Architecture

```
                 SQLite Knowledge Model (Domain Truth)
                 ┌───────────────────────────────────┐
                 │ Standard (std_is3055)             │
                 │   └── StandardVersion (ver_...)   │
                 │         └── Clause (cls_...)      │
                 └─────────────────┬─────────────────┘
                                   │
                           Explicit Join Keys
      (standard_id, version_id, knowledge_clause_id, clause_id)
                                   │
                                   ▼
                 Chroma Vector Index (Retrieval Index)
                 ┌───────────────────────────────────┐
                 │ Chunk (chunk_id)                  │
                 │  - schema_version = "6.0"         │
                 │  - standard_relation: identity    │
                 │  - source_content (literal)       │
                 │  - contextualized_content         │
                 └───────────────────────────────────┘
```

A chunk from IS 3055 Third Edition Clause 4.1 contains:
- `standard_id`: `"std_04930bebc045ee3b"`
- `version_id`: `"ver_35147ae5b42d7296"`
- `knowledge_clause_id`: `"cls_0798be1bb9f1165a"`
- `clause_id`: `"4.1"`

Two different versions of the same standard with Clause 4.1 have identical `clause_id = "4.1"` (for query intent matching) but unique, version-scoped `knowledge_clause_id` values.

---

## G. Readiness / Integrity Guard

Startup readiness in [`app/main.py`](file:///E:/Bis-system/app/main.py) and [`app/index_integrity.py`](file:///E:/Bis-system/app/index_integrity.py):
- `check_index_integrity(collection)` inspects the actual collection contents.
- Samples up to 50 chunks, inspecting `schema_version`, key count, required fields, and data types.
- Evaluates states:
  - `INDEX_READY`: Collection populated, 100% of sample matches schema 6.0.
  - `INDEX_EMPTY`: Collection legitimately empty (fresh installation).
  - `INDEX_SCHEMA_MISMATCH`: Populated, but carries stale schema. Returns HTTP 503 on `/ready`.
  - `INDEX_CORRUPT`: Current schema declared, but required fields missing or types invalid.
  - `INDEX_UNAVAILABLE`: Path unreadable.

---

## H. Re-Index Command

[`app/scripts/reindex.py`](file:///E:/Bis-system/app/scripts/reindex.py) provides safe, deterministic rebuilding:
- Command: `python -m app.scripts.reindex --rebuild`
- Probe mode: `python -m app.scripts.reindex --probe`
- Dry run: `python -m app.scripts.reindex --dry-run`

### Atomic Staging Safety
1. Discovers authoritative source PDFs in `data/raw/`.
2. Ingests documents into a temporary staging index (`data/vector_db.rebuild`).
3. Runs `check_index_integrity()` on the staging index.
4. If valid, renames live `data/vector_db` to `data/vector_db.backup-<timestamp>`.
5. Promotes staging directory to `data/vector_db`.
6. Retries up to 8 times with delays to handle Windows file lock latencies.
7. If any error occurs prior to swap, the live index remains completely untouched.

---

## I. Re-Index Results (Direct Live Store Probe)

Executing `python -m app.scripts.reindex --probe` against `data/vector_db`:

```
==============================================================
PERSISTED INDEX PROBE  (collection: bis_documents)
==============================================================
total chunks            : 22
expected schema_version : 6.0
schema_version dist     : {'6.0': 22}
metadata key histogram  : {38: 22}
standard_relation dist  : {'none': 22}
document_type dist      : {'<unknown>': 22}
context method dist     : {'structural': 22}
distinct documents      : 2

stale-schema chunks: 0
==============================================================
integrity state: INDEX_READY  healthy=True
  22 chunks indexed; 22 sampled all at schema 6.0.
```

---

## J. Persisted Index Schema Distribution

- Total chunks in live store: **22**
- Schema `6.0`: **22 (100.0%)**
- Older schemas (4-key or 28-key): **0 (0.0%)**
- Exact key count: **38 keys across all 22 chunks (100.0%)**

---

## K. Provenance Coverage

Coverage measured across all 22 chunks in `data/vector_db` (sentinels excluded):

| Provenance Field | Count / Total | Percentage |
|---|---|---|
| `chunk_id` | 22 / 22 | 100.0% |
| `document_id` | 22 / 22 | 100.0% |
| `source_hash` | 22 / 22 | 100.0% |
| `source_file` | 22 / 22 | 100.0% |
| `page_start` | 22 / 22 | 100.0% |
| `page_end` | 22 / 22 | 100.0% |
| `chunk_index` | 22 / 22 | 100.0% |
| `section` | 22 / 22 | 100.0% |
| `heading_context` | 20 / 22 | 90.9% |
| `standard_title` | 22 / 22 | 100.0% |

*(Note: In the live store, `standard_id`, `version_id`, `clause_id`, and `standard_number` are unpopulated (0.0%) because the indexed files are Gazette orders, not Indian Standards. This accurately reflects the documents without false attribution).*

---

## L. Contextualization Coverage

- `contextualized_content`: **22 / 22 (100.0%)**
- `source_content`: **22 / 22 (100.0%)**
- `context_generation_method`: **"structural" (100.0%)**
- `context_generation_version`: **"1.0" (100.0%)**

---

## M. LLM Contextualization Fix

In [`app/rag/contextualizer.py`](file:///E:/Bis-system/app/rag/contextualizer.py#L317-L388):
- Previously called `client.generate()`, which raised `AttributeError` against `GroqClient`.
- Fixed to call `client.chat_completion(messages=[...], model=..., temperature=0.0)`.
- Verified with integration test using a mocked `GroqClient` interface:
  - Valid LLM response produces `[AI-Generated Context: ...]` prefix.
  - If LLM fails or is disabled, system falls back safely to `generate_structural_context()`.
  - Authoritative `source_content` is never mutated.

---

## N. Cache-Key Correction

In [`app/rag/contextualizer.py`](file:///E:/Bis-system/app/rag/contextualizer.py#L76-L145):
- Cache key previously only included `source_hash`, `chunk_id`, `context_version`, `method`.
- Fixed: Now encodes a JSON digest of:
  - `source_hash`, `chunk_id`, `context_version`, `method`, `max_chars`, `llm_model`
  - SHA-256 `content_digest`
  - All 18 contextual inputs (`CONTEXT_INPUT_FIELDS`), including `section`, `heading_context`, `clause_id`, `standard_number`, `edition_or_version`, and page ranges.
- Guarantees identical text under different headings cannot collide in cache.

---

## O. Source Formatting Fix

In [`app/rag/source_format.py`](file:///E:/Bis-system/app/rag/source_format.py):
- Replaced dead reads `metadata.get("standard")` and `metadata.get("title")`.
- Reads canonical `standard_number` and `standard_title`.
- `format_source_header()` correctly formats grounding text for LLM generation.
- `build_sources()` creates API source dicts with explicit standard identity and citation strings (e.g. `IS 3055:2024, Third Edition, Clause 4.1, p. 3`).
- Distinguishes standard identity from references: if a document cites a standard (`standard_relation = "reference"`), it labels it as `Cites standard: ...` to avoid misattribution.

---

## P. Endpoint Integration Tests

In [`tests/test_api_endpoints.py`](file:///E:/Bis-system/tests/test_api_endpoints.py) (23 tests):
- `POST /upload`: Tested with real background worker `_ingest_background` (not mocked).
  - Valid PDF upload -> job created -> background processing runs -> job status `completed` -> chunks indexed in Chroma at schema `6.0`.
  - Rejects non-PDF files (HTTP 400).
  - Rejects files exceeding size limit (HTTP 413).
  - Rejects concurrent requests exceeding semaphore capacity (HTTP 429).
- `POST /query`:
  - Returns HTTP 200 with populated answer and citable sources.
  - Implements and bounds `top_k` (e.g. `top_k=2` returns 2 chunks; rejects `top_k=0` or negative with HTTP 400).
  - Explicitly rejects `document_ids` with HTTP 400 explaining that global BM25 cannot be partitioned without degraded recall.

---

## Q. Security Fixes Implemented & Known Blockers

### Implemented Concrete Security Protections
1. **Bounded Upload Streaming**: `_read_bounded()` streams upload bodies in 1MB chunks and halts with HTTP 413 if payload exceeds `settings.MAX_UPLOAD_SIZE_MB` (50MB), preventing RAM exhaustion.
2. **Magic Byte Verification**: Requires `%PDF-` signature within first 1024 bytes. Disallows renamed executables.
3. **Ingestion Concurrency Bounding**: `_INGESTION_SLOTS = threading.BoundedSemaphore(settings.MAX_CONCURRENT_INGESTIONS)` bounds simultaneous PDF conversion and LLM structuring, shedding excess load with HTTP 429.
4. **Filename Sanitization**: `_safe_filename()` strips directory traversal paths and null bytes.

### Known Production Blocker (Documented)
- **Zero Authentication**: Endpoints (`/upload`, `/query`, `/status`) have no authentication or API keys. Anyone with network access can upload files or trigger queries. Authentication must be implemented prior to internet exposure.

---

## R. Real Evaluation Dataset

Stored at [`data/evaluation/dataset.json`](file:///E:/Bis-system/data/evaluation/dataset.json).
Total: **25 curated queries** across all 9 required categories:

| Category | Query Count | Target in Corpus | Example Query |
|---|---|---|---|
| **exact standard** | 3 | Real / Absent | "Bureau of Indian Standards Act", "IS 3055" |
| **standard + year** | 2 | Real / Absent | "Bureau of Indian Standards Act 2016", "IS 3055:2024" |
| **clause** | 3 | Real / Absent | "Section 14 sub-section (3)", "Clause 4.1" |
| **amendment** | 3 | Real | "Third Amendment Order 2026", "तीसरा संशोधन आदेश 2026" |
| **semantic requirement**| 3 | Real | "Hallmarking of Gold Jewellery and Gold Artefacts" |
| **reference** | 4 | Real | "S.O. 4345(E)", "F. No. V-6/1/2017-BIS(Part-2)" |
| **Hindi/English** | 3 | Real | "उपभोक्ता मामले, खाद्य और सार्वजनिक वितरण मंत्रालय" |
| **mixed language** | 2 | Real | "असाधारण EXTRAORDINARY PART II-Section 3" |
| **distractor numeric** | 2 | Distractor (0 expected) | "S.O. 9999(E)", "777 of 2099" |

---

## S. Actual Retrieval Results on Rebuilt Index

### 1. Benchmark Across Real Evaluation Dataset (Live Store)
Evaluated across all 25 queries using `HybridRetriever` and `Reranker`:

```
CATEGORY BREAKDOWN (Real Persisted Index)
======================================================================
exact standard             (n=3) | R@1: 0.83 | R@3: 0.83 | R@5: 0.83 | MRR: 1.00
standard + year            (n=2) | R@1: 0.75 | R@3: 1.00 | R@5: 1.00 | MRR: 1.00
clause                     (n=3) | R@1: 0.83 | R@3: 1.00 | R@5: 1.00 | MRR: 1.00
amendment                  (n=3) | R@1: 0.50 | R@3: 0.67 | R@5: 1.00 | MRR: 0.73
semantic requirement       (n=3) | R@1: 0.50 | R@3: 0.83 | R@5: 0.83 | MRR: 0.83
reference                  (n=4) | R@1: 0.38 | R@3: 0.50 | R@5: 1.00 | MRR: 0.60
Hindi/English              (n=3) | R@1: 0.67 | R@3: 0.67 | R@5: 0.67 | MRR: 0.67
mixed language             (n=2) | R@1: 0.25 | R@3: 0.83 | R@5: 1.00 | MRR: 0.75
distractor numeric queries (n=2) | R@1: 1.00 | R@3: 1.00 | R@5: 1.00 | MRR: 1.00
----------------------------------------------------------------------
OVERALL (Queries with expected targets, n=19):
Recall@1: 0.5000 | Recall@3: 0.7193 | Recall@5: 0.8947 | MRR@5: 0.7684
```

### 2. Section 23 Required Queries on Live Store
- **Exact standard**: Query `"Bureau of Indian Standards Act"` retrieves Order S.O. 4345(E) citing the Act at Rank 1 (Score: 4.64, `ranking_reason=semantic_match`).
- **Explicit section**: Query `"Section 14 sub-section (3)"` retrieves the powers conferring paragraph at Rank 1 (Score: 5.33).
- **Semantic requirement**: Query `"Hallmarking of Gold Jewellery and Gold Artefacts"` retrieves the Order at Rank 1 (Score: 5.55).
- **Amendment query**: Query `"Third Amendment Order 2026"` retrieves the title paragraph at Rank 1 (Score: 1.83).
- **Absent standard / clause queries**: Query `"IS 3055"` and `"IS 3055 clause 4.1"` return Gazette chunks with strongly negative CrossEncoder scores (`-10.25`, `-10.85`), with `std_number=None` and `std_id=None`, proving zero false hallucination of standard identity.

### 3. Phase 4.2 Intent-Aware Ranking on Persisted BIS Standards
Verified in `test_phase42_intent_aware_ranking_on_persisted_store`:
- When Indian Standards are persisted on disk (IS 3055 fixture with Clauses 4 and 4.1 and Amendment 1):
  - Query `"IS 3055 clause 4.1"`: Top ranked chunk is strictly Clause 4.1 (`clause_id="4.1"`), outranking generic headers with `ranking_reason="exact_clause_match:4.1"`.
  - Query `"IS 3055 amendment 1"`: Top ranked chunk is strictly Amendment 1 (`amendment_number="1"`).
  - Query `"calibration accuracy requirements"`: Top ranked chunk is Clause 4.1 (`clause_id="4.1"`).
  - Query `"IS 3055"`: All top retrieved results carry `standard_number="IS 3055"`.

---

## T. Tests Passing

Full regression suite:
```
315 passed in 79.11s
```
Zero test failures, zero skipped tests, zero warnings.

---

## U. Remaining Production Blockers

1. **Authentication / Authorization**: The API endpoints have no token or credential checks.
2. **Postgres Storage Backend**: Document and job statuses currently persist via SQLite/JSON registries rather than enterprise PostgreSQL.
3. **Corpus Expansion**: Current real documents in `data/raw/` consist only of Gazette orders; ingestion of primary Indian Standard specifications (e.g. IS 3055, IS 1570, IS 1293) is required to build out the full technical compliance knowledge graph.

---

## V. Items Not Actually Verified

- **Enterprise Concurrency under High Distributed Load**: Ingestion semaphore bounds concurrency per process; distributed worker coordination (e.g. Celery / Redis) has not been tested.
- **Production Groq API Live Quota**: Evaluated with local SentenceTransformer / CrossEncoder embeddings and mocked Groq clients; live Groq rate limits were not exercised during offline test runs.

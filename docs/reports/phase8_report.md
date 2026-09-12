# Phase 8 Engineering Report: Citation Enforcement & Grounding Validation

**Phase:** Phase 8  
**Date:** 2026-09-10  
**Status:** Complete & Verified  
**Total Tests Passing:** **381 / 381** (100% green across entire suite, 42 new tests added)  
**Safety Metric:** **0.0% False Grounded Claim Rate** (0 / 9 adversarial test cases passed through ungrounded)  
**Citation Validity Rate:** **100.0%** across live benchmark (125 / 125 citations valid)

---

## 1. Executive Summary

Phase 8 implements canonical citation traceability, structured answer claim representation, and deterministic post-generation grounding validation for the BIS compliance intelligence backend.

Key achievements:
- **Zero Hallucinated Metadata:** Citations resolve exclusively to application-controlled `EvidenceItem` objects. The LLM produces only evidence tokens (e.g. `[EV1]`), and cannot fabricate page numbers, clause IDs, standard numbers, or source URLs.
- **Authoritative Source Content Grounding:** Citations point strictly to raw, unadorned `source_content`, ensuring AI-generated contextual prefaces (`[Section: ...]`) are never cited as authoritative text.
- **Strict Factual Validation:** Enforces numerical/date integrity, clause/standard identifier compatibility, legal conclusion checks, and epistemic honesty rules (missing evidence $\neq$ negative domain facts).
- **Bounded Safe Regeneration:** Bounded to exactly 1 corrective pass when unsupported claims are detected; if the regeneration fails grounding, the system strictly transitions to `VERIFICATION_REQUIRED`.
- **Zero Overhead Grounding:** Deterministic claim validation runs in $\sim 0.001\text{ ms}$ per query with no secondary LLM calls.

---

## 2. Detailed Technical Report (Sections A – U)

### A. Files Changed & Created

| File | Action | Purpose |
| :--- | :--- | :--- |
| [`app/grounding/models.py`](file:///E:/Bis-system/app/grounding/models.py) | **Created** | Canonical `Citation`, `AnswerClaim`, `ClaimType`, `SupportStatus`, `GroundingStatus`, and `GroundingResult` models. |
| [`app/grounding/validator.py`](file:///E:/Bis-system/app/grounding/validator.py) | **Created** | Deterministic claim extraction, numerical/unit validation, identifier matching, negation checking, and grounding validator. |
| [`app/grounding/__init__.py`](file:///E:/Bis-system/app/grounding/__init__.py) | **Created** | Package exports for grounding subsystem. |
| [`app/rag/generator.py`](file:///E:/Bis-system/app/rag/generator.py) | **Modified** | Structured `[EVIDENCE EV{i}]` context headers; system prompt rules for citations/claims; structured JSON parsing with fallback; bounded repair feedback support. |
| [`app/rag/pipeline.py`](file:///E:/Bis-system/app/rag/pipeline.py) | **Modified** | Injected `GroundingValidator` post-generation; bounded single regeneration retry; combined Phase 7 confidence with Phase 8 grounding policy; populated citation/claim response fields. |
| [`app/models.py`](file:///E:/Bis-system/app/models.py) | **Modified** | Extended `QueryResponse` with backward-compatible Phase 8 fields (`citations`, `claims`, `citation_coverage`, `grounding_status`, `grounding_reason`, `groundedness_score`). |
| [`app/api/query.py`](file:///E:/Bis-system/app/api/query.py) | **Modified** | Forwarded Phase 8 grounding and citation attributes to `QueryResponse`. |
| [`tests/test_grounding.py`](file:///E:/Bis-system/tests/test_grounding.py) | **Created** | 25 failure-first unit tests covering all Section 27 requirements. |
| [`tests/test_e2e_prompt8.py`](file:///E:/Bis-system/tests/test_e2e_prompt8.py) | **Created** | 17 end-to-end and adversarial generator tests covering Section 28 & 29 scenarios. |
| [`scratch/run_prompt8_eval.py`](file:///E:/Bis-system/scratch/run_prompt8_eval.py) | **Created** | 25-query evaluation harness measuring citation validity, coverage, and latency against the live store. |
| [`scratch/prompt8_eval_results.json`](file:///E:/Bis-system/scratch/prompt8_eval_results.json) | **Created** | Persisted metrics and per-query logs from evaluation run. |

---

### B. Canonical Citation Model

Defined in [`app/grounding/models.py`](file:///E:/Bis-system/app/grounding/models.py) as `Citation`:

```python
@dataclass
class Citation:
    citation_id: str          # e.g. "EV1"
    chunk_id: str             # Deterministic chunk ID
    document_id: str          # Document ID
    source_hash: str          # SHA-256 hash of original file
    source_file: str          # Path/name of source file
    page_start: Optional[int] # Authoritative starting page
    page_end: Optional[int]   # Authoritative ending page
    standard_id: Optional[str]
    standard_number: Optional[str]
    standard_title: Optional[str]
    version_id: Optional[str]
    edition_or_version: Optional[str]
    clause_id: Optional[str]
    clause_title: Optional[str]
    source_url: Optional[str]
    authority: Optional[str]
    content_hash: Optional[str]
```

- **Traceability:** Maps directly to `document -> chunk -> exact source evidence`.
- **Anti-Hallucination:** Populated solely by the application from verified `EvidenceItem` objects. The LLM is never permitted to supply metadata.

---

### C. Answer Claim Model

Defined in [`app/grounding/models.py`](file:///E:/Bis-system/app/grounding/models.py) as `AnswerClaim`:

```python
@dataclass
class AnswerClaim:
    claim_id: str
    text: str
    claim_type: ClaimType     # FACT, INTERPRETATION, UNCERTAINTY
    citation_ids: List[str]   # e.g. ["EV1"]
    support_status: SupportStatus # SUPPORTED, PARTIALLY_SUPPORTED, UNSUPPORTED, UNVERIFIABLE
    validation_notes: Optional[str] = None
    supporting_citation_count: int = 0
    issues: List[str] = field(default_factory=list)
```

- **Controlled Vocabularies:**
  - `ClaimType`: `fact`, `interpretation`, `uncertainty`
  - `SupportStatus`: `supported`, `partially_supported`, `unsupported`, `unverifiable`

---

### D. Generator Output Contract

The generator requests structured JSON output from the LLM:

```json
{
  "answer": "Complete readable text with inline citation tags like [EV1].",
  "claims": [
    {
      "claim_id": "C1",
      "text": "Statement of claim",
      "claim_type": "fact",
      "citation_ids": ["EV1"]
    }
  ]
}
```

- **Parsing Resilience:** `AnswerGenerator._parse_structured_output` parses JSON from raw strings or markdown codeblocks.
- **Graceful Text Fallback:** If the model returns plain text (or in retrieval-only mode), `GroundingValidator.extract_claims_from_text` segments sentences, parses inline `[EVn]` tokens, and classifies claim types via linguistic heuristics.

---

### E. Grounding Validator Architecture

The validator (`GroundingValidator`) executes a deterministic multi-stage verification pipeline:

1. **Citation Existence:** Verifies every `citation_id` exists in the retrieved evidence set. Flags `fake_or_unknown_citation_id`.
2. **Chunk Deduplication:** Collapses citations referencing identical `content_hash`es so duplicate chunks are not counted as independent support.
3. **Identifier Consistency:**
   - Standard: Validates that if the claim mentions `IS XXXX`, the cited evidence has a matching standard designation.
   - Clause: Validates that if the claim mentions `Clause X.Y`, the cited evidence matches `clause_id`.
   - Version/Amendment: Checks compatibility with edition and amendment metadata.
4. **Numerical & Date Verification:**
   - Extracts quantities, units (months, mm, V, %, etc.), and standalone numbers.
   - Masks out clause and standard IDs in source text to avoid false substring matches (e.g. preventing `4` in `Clause 4.1` from validating `4 hours`).
   - Verifies both unit stem and numerical value exist in the cited evidence.
5. **Negation & Epistemic Honesty:**
   - Detects negative claims ("does not exist", "no requirement exists").
   - Requires explicit textual confirmation in source content; otherwise flags `unsupported_negative_assertion`.
6. **Regulatory Extrapolation Guard:**
   - Detects broad conclusions ("prohibited from selling", "mandatory BIS certification").
   - Requires explicit grounding in source text; otherwise flags `unsupported_legal_conclusion`.
7. **Semantic Support & Unsupported Terms:**
   - Computes overlap and missing term ratios. If $\ge 50\%$ of substantive terms are missing, flags `unsupported_terms` and marks claim `UNSUPPORTED`.

---

### F. Citation Resolution Mechanism

1. Reranked results are assigned sequential stable tokens: `EV1`, `EV2`, ..., `EVk`.
2. Generator prompt displays:
   ```
   ==================================================
   RETRIEVED SOURCE {index} [EVIDENCE EV{index}]
   ==================================================
   Standard: IS 3055
   ...
   CONTENT:
   {authoritative source text}
   ```
3. Claims generated with `citation_ids: ["EV1"]` are resolved by looking up `EV1` in the application's in-memory `citation_map`.
4. The API response returns fully hydrated `Citation` dictionaries.

---

### G. Source-Content Authority Rules

- In `AnswerGenerator.format_context`: `content` resolves strictly to `result.get("source_content") or result.get("content")`.
- In `EvidenceItem`: `source_content` stores the raw text extracted from the document.
- In `GroundingValidator`: validation is evaluated exclusively against `ev.source_content`.
- **Invariant:** Contextualized breadcrumbs (`[Standard: ... | Clause: ...]`) are used only for dense/BM25 vector scoring and are never presented or cited as source text.

---

### H. Unsupported-Claim Policy

If any factual claim receives `SupportStatus.UNSUPPORTED`:
- The claim cannot remain presented as an established fact.
- Bounded safe repair is attempted (1 attempt).
- If the repaired generation still has unsupported claims, `RAGPipeline` forces:
  - `decision = "verification_required"`
  - `verification_required = True`
  - `verification_reason = "Grounding failure: Grounding validation detected X unsupported claim(s)."`

---

### I. Partial-Support Policy

- If claims have partial support (`SupportStatus.PARTIALLY_SUPPORTED`) and no severe unsupported claims:
  - Overall status: `PARTIALLY_GROUNDED`.
  - If Phase 7 confidence allowed `ANSWER`, it is modified to `QUALIFIED_ANSWER`.
  - `verification_required` remains `False`.

---

### J. Regeneration Behavior

- **Trigger:** Initiated if `grounding.status == GroundingStatus.UNSUPPORTED` and generator is active.
- **Bound:** Exactly **1** repair attempt (`max_attempts = 1`). Infinite loops are structurally impossible.
- **Feedback Injection:** The specific unsupported claims and issues are passed into `repair_feedback` in `build_user_prompt`.
- **Termination:** If the second attempt is still unsupported, the system falls back to `VERIFICATION_REQUIRED`.

---

### K. API Response Changes

`QueryResponse` in [`app/models.py`](file:///E:/Bis-system/app/models.py) extended with optional backward-compatible fields:

```python
citations: Optional[List[Dict[str, Any]]] = None
claims: Optional[List[Dict[str, Any]]] = None
citation_coverage: Optional[float] = None
grounding_status: Optional[str] = None
grounding_reason: Optional[str] = None
groundedness_score: Optional[float] = None
```

---

### L. Citation Coverage

- **Definition:** Supported factual claims divided by total factual claims:
  $$\text{citation\_coverage} = \frac{|\text{claims}_{\text{fact}} \cap \text{claims}_{\text{supported}}|}{|\text{claims}_{\text{fact}}|}$$
- **Evaluation Benchmark Result:** **1.00 (100.0%)** on fully grounded answers; **0.00** on ungrounded/abstention queries.
- **Note:** Never described as "accuracy".

---

### M. Unsupported Claim Rate

- **Definition:** Percentage of generated claims flagged as unsupported by the validator:
  $$\text{unsupported\_claim\_rate} = \frac{|\text{claims}_{\text{unsupported}}|}{|\text{claims}_{\text{total}}|}$$
- **Evaluation Benchmark Result:** **0.0%** on the standard evaluation queries; **100.0%** on adversarial fabrication queries.

---

### N. False Grounded-Claim Rate

- **Definition:** Factual claims presented as supported when they are not actually supported by cited evidence:
  $$\text{false\_grounded\_rate} = \frac{|\text{claims}_{\text{unsupported \ but \ accepted}}|}{|claims_{total}|}$$
- **Result:** **0.0%** across both the 25-query evaluation benchmark and the 9-case adversarial generator test suite.

---

### O. Adversarial Test Results

Tested in [`tests/test_e2e_prompt8.py::test_adversarial_generator_cases`](file:///E:/Bis-system/tests/test_e2e_prompt8.py):

| Adversarial Attack Case | Injected Hallucination | Validator Detection | Status |
| :--- | :--- | :--- | :--- |
| `invented_clause` | Clause 9.9 requires recalibration | `clause_mismatch:claim=Clause 9.9` | **REJECTED** |
| `invented_date` | Testing shall commence in year 2045 | `unsupported_numerical_value:2045` | **REJECTED** |
| `invented_frequency` | Error verification done every 4 hours | `unsupported_numerical_value:4 hours` | **REJECTED** |
| `invented_standard` | IS 9999 requires 0.1 deg C tolerance | `standard_mismatch:claim=IS 9999` | **REJECTED** |
| `broad_legal_conclusion` | Thermometers illegal to sell | `unsupported_legal_conclusion` | **REJECTED** |
| `unsupported_negative_claim`| No requirement exists for calibration | `unsupported_negative_assertion` | **REJECTED** |
| `fake_citation_id` | Permissible error [EV99] | `fake_or_unknown_citation_id:EV99` | **REJECTED** |
| `wrong_citation_attribution`| Ordinary Portland Cement 33 MPa [EV1] | `unsupported_terms` | **REJECTED** |
| `partially_true_with_hallucination` | Error 0.1 C under boiling cryogenic fluid | `unsupported_terms` | **REJECTED** |

**Result:** 9 out of 9 adversarial attacks caught and rejected (**100% catch rate**, **0.0% false grounded rate**).

---

### P. Latency Measurements

Measured across 25 live queries in [`scratch/prompt8_eval_results.json`](file:///E:/Bis-system/scratch/prompt8_eval_results.json):

- **Average Total Query Latency:** $2,808.3\text{ ms}$ (dominated by CrossEncoder reranking and Groq LLM generation)
- **Average Claim Validation Time:** **$0.001\text{ ms}$** per query ($< 1\text{ }\mu\text{s}$ per claim)
- **Validation Overhead:** Negligible ($< 0.0001\%$ of total pipeline latency).

---

### Q. Tests Added

- **`tests/test_grounding.py`:** 25 unit tests covering Section 27 failure-first requirements.
- **`tests/test_e2e_prompt8.py`:** 17 integration and adversarial tests covering Sections 28 and 29.
- **Total New Tests Added:** **42 tests**.

---

### R. Total Tests Passing

$$\mathbf{381 \ / \ 381 \ tests \ passing \ (100\% \ green)}$$

Full regression execution completed in $115.23\text{s}$ via `py -3.11 -m pytest tests/ -q`.

---

### S. Real End-to-End Examples

#### Example 1: Fully Grounded Query
- **Query:** `"IS 3055 clause 4.1"`
- **Response Decision:** `"answer"`
- **Grounding Status:** `"fully_grounded"`
- **Groundedness Score:** `1.00`
- **Citation Coverage:** `1.00`
- **Citations:**
  ```json
  [
    {
      "citation_id": "EV1",
      "chunk_id": "c_41",
      "standard_number": "IS 3055",
      "clause_id": "4.1",
      "edition_or_version": "Third Edition",
      "page_start": 3,
      "page_end": 3,
      "source_file": "standards/IS_3055.pdf"
    }
  ]
  ```

#### Example 2: Distractor Query (Absent Standard)
- **Query:** `"IS 9999 requirement for gold plating"`
- **Response Decision:** `"verification_required"`
- **Grounding Status:** `"fully_grounded"` (abstention statement correctly acknowledges limits)
- **Grounding Reason:** `"No extractable claims in generated answer."`
- **Verification Reason:** `"Standard 'IS 9999' was not found in the ingested documentation."`

---

### T. Known Limitations

1. **Table OCR Noise:** Complex scanned annexure tables with irregular formatting may have partial word overlap if OCR introduces character errors.
2. **Single Regeneration Attempt:** The repair loop is strictly bounded to 1 retry. If an adversarial or noisy model cannot correct its claims in 1 pass, it immediately transitions to `verification_required`.

---

### U. Verification Checklist

- [x] All 25 failure-first unit tests implemented and passing.
- [x] All Section 28 E2E scenarios (A–F) verified against live index.
- [x] All Section 29 adversarial generator cases caught with 0% false grounded claim rate.
- [x] API response contract backward compatibility preserved.
- [x] Total test suite regression passing (381 / 381).
- [x] Phase 8 complete; no Phase 9 features started.

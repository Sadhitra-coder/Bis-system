# BIS RAG Document Processing Pipeline - Refactoring Summary

## Overview

The document processing pipeline has been completely refactored from a hardcoded, monolithic system into a **generic, reusable, modular architecture** that supports:

- ✅ Multiple PDF folders and categories
- ✅ Recursive PDF discovery
- ✅ Preserved relative directory structure
- ✅ Large document handling with intelligent chunking
- ✅ Graceful error handling with detailed reporting
- ✅ Production-ready logging and monitoring

---

## Architecture Changes

### BEFORE (Problematic)
```
Individual step modules contained:
- Hardcoded paths (e.g., INPUT_DIR, OUTPUT_DIR)
- Directory scanning logic (glob, rglob)
- Monolithic "process all files" functions
- No clear orchestration

Pipeline:
- No path preservation
- Brittle and inflexible
- Difficult to scale to multiple document categories
```

### AFTER (Refactored)
```
Each step module is INDEPENDENT:
- Takes input_path and output_path as parameters
- No hardcoded directory logic
- Processes ONE file at a time
- Pure functions with clear contracts

Master Pipeline Orchestrator:
- Discovers all PDFs recursively
- Determines relative paths
- Creates output directory structures
- Calls each step in sequence
- Handles errors gracefully
- Provides comprehensive reporting
```

---

## Module Refactoring Details

### 1. **extract.py** - PDF Extraction

**Changes:**
- ✅ Removed hardcoded `output_dir = Path("data/markdown")`
- ✅ New signature: `extract_pdf(input_path: Path, output_path: Path) -> Path`
- ✅ Creates output directories automatically
- ✅ Proper error handling with custom exceptions

**Usage:**
```python
from app.steps.extract import extract_pdf

extract_pdf(
    input_path=Path("data/raw/clinical/thermometer/manual.pdf"),
    output_path=Path("data/markdown/clinical/thermometer/manual.md")
)
```

---

### 2. **clean.py** - Lossless Markdown Cleaning

**Changes:**
- ✅ Removed hardcoded paths
- ✅ New signature: `clean_markdown_file(input_path: Path, output_path: Path) -> Dict`
- ✅ Preserves ALL document information (no summarization, no deduplication)
- ✅ Safe, verified formatting cleanup only

**Cleaning Operations (Lossless):**
1. Normalize line endings
2. Decode HTML entities
3. Normalize Unicode spaces
4. Remove zero-width characters
5. Remove trailing whitespace
6. Collapse excessive blank lines (4+ → 3)

**NOT Changed:**
- Document meaning
- Content
- Headings, tables, structure

---

### 3. **structure.py** - AI Document Structuring

**Major Improvements:**
- ✅ Removed hardcoded `INPUT_DIR` and `OUTPUT_DIR`
- ✅ New signature: `structure_markdown_file(input_path: Path, output_path: Path, document_id: Optional[str]) -> Path`
- ✅ **Large document handling** - intelligently splits and processes in chunks if exceeds ~100k tokens
- ✅ Uses Groq API safely with context window awareness

**Large Document Strategy:**
```
If document > 100k tokens:
1. Split by section headings (preserves hierarchy)
2. Process each chunk independently with Groq
3. Reassemble with proper spacing
4. No information is lost
```

**Context Limits:**
- Groq context window: ~128k tokens
- Safety margin: Process chunks of ~50k tokens
- Complete document limit: ~100k tokens (400k chars)

---

### 4. **normalize.py** - Markdown Escaping Normalization

**Changes:**
- ✅ Removed hardcoded `INPUT_DIR` and `OUTPUT_DIR`
- ✅ New signature: `normalize_markdown_file(input_path: Path, output_path: Path) -> Path`
- ✅ **Conservative artifact fixing** - only removes VERIFIED escaping artifacts
- ✅ Never removes legitimate Markdown escaping

**What Gets Fixed:**
- Escaped pipes in tables: `\|` → `|`
- URL-encoded escaped characters: `%5C|` → `|`

**What DOESN'T Get Fixed:**
- Escaped asterisks: `\*` (could be legitimate)
- Escaped dots: `\.` (could be legitimate)
- Escaped brackets: `\<` or `\>` (used in some contexts)

**Philosophy:**
> "Only fix artifacts we're 100% sure are mistakes. Don't risk breaking legitimate escaping."

---

### 5. **chunk.py** - Semantic Document Chunking

**Changes:**
- ✅ Removed hardcoded `INPUT_DIR` and `OUTPUT_DIR`
- ✅ New signature: `chunk_markdown_file(input_path: Path, output_path: Path, document_id: str, source_file: str) -> Path`
- ✅ Smart section parsing and merging
- ✅ Intelligent table handling
- ✅ Deterministic chunk IDs

**Chunking Pipeline:**
```
1. Parse sections (preserve hierarchy)
2. Merge heading-only sections
3. Merge tiny sections (< 250 chars)
4. Split large sections (> 4000 chars)
   - Preserve tables
   - Split on paragraph boundaries
   - Never remove content
5. Generate chunk metadata
```

**Chunk Metadata:**
```json
{
  "chunk_id": "manual_chunk_00001",
  "document_id": "manual",
  "source_file": "clinical/thermometer/manual.pdf",
  "section": "Installation",
  "heading_context": ["Device Setup", "Installation"],
  "content": "...",
  "character_count": 2847
}
```

---

### 6. **pipeline.py** - Master Orchestrator

**Complete Rewrite:**
- ✅ Centralized directory management
- ✅ Recursive PDF discovery with `rglob("*.pdf")`
- ✅ Relative path preservation through all stages
- ✅ Clean filename strategy (no suffix accumulation)
- ✅ Comprehensive error handling
- ✅ Detailed execution summaries
- ✅ Sequential processing with graceful failure handling

**Pipeline Flow:**
```
1. DISCOVERY: Find all PDFs in data/raw/**/
2. FOR EACH PDF:
   a. Calculate relative path (preserve directory structure)
   b. STEP 1: Extract (PDF → Markdown)
   c. STEP 2: Clean (Markdown → Cleaned)
   d. STEP 3: Structure (Cleaned → Structured via Groq)
   e. STEP 4: Normalize (Structured → Normalized)
   f. STEP 5: Chunk (Normalized → JSON chunks)
   g. Log success or capture error
3. SUMMARY: Report statistics and failures
4. EXIT: Code 0 (success) or 1 (failures)
```

---

## Directory Structure Preservation

### INPUT
```
data/raw/
├── clinical/
│   ├── thermometer/
│   │   ├── manual.pdf
│   │   └── standard.pdf
│   └── syringe/
│       └── manual.pdf
├── electrical/
│   └── cables/
│       └── cable_manual.pdf
└── mechanical/
    └── valves/
        └── valve.pdf
```

### OUTPUTS (Structure Preserved)
```
data/markdown/
├── clinical/thermometer/manual.md
├── clinical/thermometer/standard.md
├── clinical/syringe/manual.md
├── electrical/cables/cable_manual.md
└── mechanical/valves/valve.md

data/cleaned/
├── clinical/thermometer/manual_cleaned.md
├── clinical/thermometer/standard_cleaned.md
├── clinical/syringe/manual_cleaned.md
├── electrical/cables/cable_manual_cleaned.md
└── mechanical/valves/valve_cleaned.md

data/structured/
├── clinical/thermometer/manual_structured.md
├── clinical/thermometer/standard_structured.md
├── clinical/syringe/manual_structured.md
├── electrical/cables/cable_manual_structured.md
└── mechanical/valves/valve_structured.md

data/normalized/
├── clinical/thermometer/manual_normalized.md
├── clinical/thermometer/standard_normalized.md
├── clinical/syringe/manual_normalized.md
├── electrical/cables/cable_manual_normalized.md
└── mechanical/valves/valve_normalized.md

data/chunks/
├── clinical/thermometer/manual_chunks.json
├── clinical/thermometer/standard_chunks.json
├── clinical/syringe/manual_chunks.json
├── electrical/cables/cable_manual_chunks.json
└── mechanical/valves/valve_chunks.json
```

---

## Filename Strategy

Clean, deterministic naming without suffix accumulation:

| Step | Input Pattern | Output Pattern | Example |
|------|---------------|----------------|---------|
| Extract | `manual.pdf` | `{name}.md` | `manual.md` |
| Clean | `manual.md` | `{name}_cleaned.md` | `manual_cleaned.md` |
| Structure | `manual_cleaned.md` | `{name}_structured.md` | `manual_structured.md` |
| Normalize | `manual_structured.md` | `{name}_normalized.md` | `manual_normalized.md` |
| Chunk | `manual_normalized.md` | `{name}_chunks.json` | `manual_chunks.json` |

**Note:** Each step starts from the ORIGINAL filename stem, not accumulating suffixes.
- ✅ CORRECT: `manual_structured.md`
- ❌ INCORRECT (old): `manual_cleaned_structured.md`
- ❌ INCORRECT (old): `manual_cleaned_structured_normalized.md`

---

## Bug Fixes

### Fixed Issues

1. **Regex Pattern Bugs**
   - ✅ Heading detection: Correct pattern `r"^\s{0,3}#{1,6}\s+\S"`
   - ✅ Heading level: Correct extraction `r"^\s*(#{1,6})\s+"`
   - ✅ Blank line normalization: `r"\n{4,}"` → `"\n\n\n"`

2. **Large Document Handling**
   - ✅ Added context window awareness for Groq API
   - ✅ Automatic splitting of documents > 100k tokens
   - ✅ Section-preserving chunking strategy

3. **Normalization Conservatism**
   - ✅ No longer aggressively replaces all backslashes
   - ✅ Only fixes VERIFIED artifacts
   - ✅ Preserves legitimate Markdown escaping

4. **Path Hardcoding**
   - ✅ Removed all hardcoded directory paths from step modules
   - ✅ Master pipeline controls directory logic
   - ✅ Each step is pure and reusable

5. **Embedding Compatibility**
   - ✅ Updated embed.py to use `rglob()` instead of `glob()`
   - ✅ Chunk files discovered recursively across categories
   - ✅ Unique chunk IDs ensure no overwrites

---

## Usage

### Running the Complete Pipeline

```bash
# From project root
python -m app.steps.pipeline
```

### Output
```
======================================================================
STARTING BIS DOCUMENT PROCESSING PIPELINE
======================================================================
PDFs discovered: 5

======================================================================
PROCESSING PDF: clinical/thermometer/manual.pdf
======================================================================
2026-09-02 14:32:15 | INFO | STEP 1 EXTRACT | input=manual.pdf | output=manual.md
2026-09-02 14:32:16 | INFO | Extraction successful | file=manual.md | characters=45000
2026-09-02 14:32:17 | INFO | STEP 2 CLEAN | input=manual.md | output=manual_cleaned.md
2026-09-02 14:32:18 | INFO | Cleaned Markdown saved | path=manual_cleaned.md
2026-09-02 14:32:19 | INFO | STEP 3 STRUCTURE | input=manual_cleaned.md | output=manual_structured.md
2026-09-02 14:32:45 | INFO | Structured Markdown saved | file=manual_structured.md | input_chars=45000 | output_chars=44800
2026-09-02 14:32:46 | INFO | STEP 4 NORMALIZE | input=manual_structured.md | output=manual_normalized.md
2026-09-02 14:32:47 | INFO | Normalization successful | file=manual_normalized.md | before=44800 chars | after=44792 chars
2026-09-02 14:32:48 | INFO | STEP 5 CHUNK | input=manual_normalized.md | output=manual_chunks.json
2026-09-02 14:32:49 | INFO | Chunking successful | file=manual_chunks.json | chunks=12
2026-09-02 14:32:49 | INFO | PIPELINE COMPLETE FOR: clinical/thermometer/manual.pdf

======================================================================
PIPELINE SUMMARY
======================================================================
Total PDFs discovered: 5
Successfully processed: 5
Failed: 0
Skipped: 0
======================================================================
```

---

## Error Handling

If a PDF fails, the pipeline continues processing others:

```
======================================================================
PIPELINE SUMMARY
======================================================================
Total PDFs discovered: 5
Successfully processed: 4
Failed: 1
Skipped: 0

FAILED PDFs:
  - data/raw/broken/corrupt.pdf: Failed to extract PDF: PDF parsing error

======================================================================
```

Pipeline exits with:
- **Code 0**: All PDFs processed successfully
- **Code 1**: One or more failures

---

## Configuration

### Chunk Size Settings
Located in `app/steps/chunk.py`:
```python
MAX_CHUNK_CHARS = 4000       # Maximum chunk size
MIN_CHUNK_CHARS = 250        # Minimum before merging
```

### Groq Context Settings
Located in `app/steps/structure.py`:
```python
GROQ_CONTEXT_LIMIT_CHARS = 400000    # ~100k tokens
DOCUMENT_CHUNK_SIZE_CHARS = 200000   # ~50k token chunks
```

### Logging
Pipeline uses standard Python logging:
```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
```

---

## Requirements

### Python Packages
```
docling                  # PDF extraction
groq                     # Groq LLM API
python-dotenv           # Environment variable loading
```

### Environment Variables
```bash
# .env file (in project root)
GROQ_API_KEY=your_key_here
```

### Directory Structure
```
bis-rag-engine/
├── data/
│   ├── raw/              # Input PDFs (create subdirectories as needed)
│   ├── markdown/         # Step 1 output (auto-created)
│   ├── cleaned/          # Step 2 output (auto-created)
│   ├── structured/       # Step 3 output (auto-created)
│   ├── normalized/       # Step 4 output (auto-created)
│   └── chunks/           # Step 5 output (auto-created)
├── app/
│   └── steps/
│       ├── extract.py
│       ├── clean.py
│       ├── structure.py
│       ├── normalize.py
│       ├── chunk.py
│       └── pipeline.py
```

---

## Migration Guide

### From Old Code
If you had custom code calling individual steps:

**OLD:**
```python
from app.steps.clean import clean_markdown_file

# Had to manually manage paths
result = clean_markdown_file(
    input_path="data/markdown/file.md",
    output_path="data/cleaned/file_cleaned.md"
)
```

**NEW:**
```python
from app.steps.clean import clean_markdown_file
from pathlib import Path

# Pass proper Path objects with full paths
result = clean_markdown_file(
    input_path=Path("data/markdown/clinical/thermometer/file.md"),
    output_path=Path("data/cleaned/clinical/thermometer/file_cleaned.md")
)
```

**OR (Recommended):**
Use the master pipeline to manage everything:
```python
from app.steps.pipeline import run_pipeline

# Run complete orchestration
run_pipeline()
```

---

## Production Readiness Checklist

- ✅ Generic, reusable module design
- ✅ No hardcoded paths in step modules
- ✅ Recursive PDF discovery
- ✅ Directory structure preservation
- ✅ Large document handling
- ✅ Conservative, verified bug fixes
- ✅ Comprehensive error handling
- ✅ Detailed logging and reporting
- ✅ Deterministic chunk IDs
- ✅ Graceful failure handling

---

## Summary

The refactored pipeline is now:

1. **Generic** - Works with any number of documents in any directory structure
2. **Reusable** - Each module can be used independently or as part of the orchestration
3. **Scalable** - Handles large documents with intelligent chunking
4. **Robust** - Comprehensive error handling and reporting
5. **Production-Ready** - Proper logging, validation, and clean architecture

The master pipeline orchestrator makes it easy to process hundreds or thousands of documents while preserving your directory structure and tracking progress.

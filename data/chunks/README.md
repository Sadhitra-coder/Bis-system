# Chunks & Metadata Store

This directory contains the authoritative chunk dataset and regulatory metadata exported directly from the primary vector collection (`bis_documents`).

## File Overview

- **`all_chunks_metadata.json`**: Complete dump of all **193 text chunks** along with all **38 metadata fields** per chunk, preserved in structured JSON format.

## Metadata Schema (38 Attributes)

Each chunk entry includes:
- `id`: Unique chunk identifier (e.g., `doc_...`)
- `document`: Full textual content of the clause/section chunk
- `metadata`:
  - `standard_number`: BIS standard code (e.g. `IS 9873`, `IS 3055-1`, `IS 1293`, `IS 16444`)
  - `document_id`: Source document unique hash
  - `knowledge_clause_id`: Identifier linking to the relational knowledge graph in `data/knowledge/bis_knowledge.db`
  - `section` / `clause_number`: Exact clause or section hierarchy
  - `page_start` / `page_end` / `page_number`: Page references in the source regulatory PDF
  - `amendment`: Amendment number/status if applicable
  - `is_current`: Boolean indicating active status
  - `parser_version`: Document pipeline parser version
  - `context_generation_method`: Contextual expansion metadata
  - `char_offset_start`: Exact character position offset in source text

## Loading & Inspecting Chunks

In Python:
```python
import json

with open("data/chunks/all_chunks_metadata.json", "r", encoding="utf-8") as f:
    chunks = json.load(f)

print(f"Total indexed chunks: {len(chunks)}")
print(f"First chunk ID: {chunks[0]['id']}")
print(f"Standard: {chunks[0]['metadata'].get('standard_number')}")
```

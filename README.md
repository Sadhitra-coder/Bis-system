# bis-rag-engine

A modular document ingestion and retrieval pipeline built for structured document processing, chunking, embedding, and retrieval augmented generation.

## Project structure

```text
bis-rag-engine/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   ├── api/
│   │   ├── upload.py
│   │   ├── query.py
│   │   └── status.py
│   ├── workflows/
│   │   ├── __init__.py
│   │   ├── document_ingestion.py
│   │   └── events.py
│   ├── steps/
│   │   ├── __init__.py
│   │   ├── extract.py
│   │   ├── clean.py
│   │   ├── structure.py
│   │   ├── validate.py
│   │   ├── chunk.py
│   │   ├── embed.py
│   │   └── store.py
│   ├── agents/
│   │   ├── __init__.py
│   │   └── document_structuring_agent.py
│   ├── prompts/
│   │   ├── __init__.py
│   │   └── document_structure_prompt.py
│   ├── rag/
│   │   ├── retriever.py
│   │   ├── reranker.py
│   │   └── generator.py
│   └── database/
│       ├── postgres.py
│       └── vector_store.py
├── data/
│   ├── raw/
│   ├── markdown/
│   ├── structured/
│   └── chunks/
├── tests/
│   ├── test_ingestion.py
│   ├── test_structuring.py
│   └── test_rag.py
├── requirements.txt
├── .env
└── README.md
```

## Getting started

1. Create and activate a virtual environment.
2. Install dependencies from `requirements.txt`.
3. Configure environment variables in `.env`.
4. Run the application through `app/main.py`.

## Notes

This scaffold is intentionally modular so each pipeline stage can be developed independently and plugged into the main orchestration flow.

# BIS Metadata Discovery Engine

This project implements the first milestone of the BIS GPT architecture: a metadata-first crawler and PostgreSQL document registry.

## Milestone 1 goals

- Discover BIS pages and document links
- Normalize and deduplicate URLs
- Store source, page, and document metadata in PostgreSQL
- Provide a minimal API for listing and checking documents
- Keep the crawler intentionally metadata-focused; no mass PDF download or embedding yet

## Project structure

```text
bis-rag-engine/
├── app/
│   ├── api/
│   ├── config.py
│   ├── crawler/
│   │   ├── crawler.py
│   │   ├── metadata.py
│   │   └── __init__.py
│   ├── database/
│   │   ├── models.py
│   │   ├── session.py
│   │   └── __init__.py
│   ├── main.py
│   └── __init__.py
├── scripts/
│   └── crawl_bis.py
├── tests/
│   └── test_ingestion.py
├── .env.example
├── docker-compose.yml
├── docker/
│   └── Dockerfile
├── requirements.txt
└── README.md
```

## Setup

1. Create a virtual environment.
2. Copy `.env.example` to `.env` and adjust values for your environment.
3. Start PostgreSQL locally or via Docker Compose.
4. Run database initialization with the app startup or directly via the app.
5. Execute the crawl entrypoint:

```bash
python scripts/crawl_bis.py
```

## Crawl behavior

The crawler currently:

- starts from the configured BIS base URL
- fetches internal pages
- extracts internal links
- identifies PDF or document candidates
- normalizes and deduplicates URLs
- stores basic page and document metadata in PostgreSQL

It is intentionally limited to metadata discovery, not full document processing.

## Database schema

Key tables include:

- `sources`
- `pages`
- `documents`
- `document_versions`
- `document_relationships`

These are created automatically through SQLAlchemy metadata creation when the app starts.

## API

The app exposes basic endpoints for:

- `GET /health`
- `GET /api/v1/documents`
- `GET /api/v1/documents/{id}`
- `GET /api/v1/documents/{id}/status`
- `POST /api/v1/crawl`
- `POST /api/v1/documents/{id}/process`

## Next milestone

After verification of the metadata layer, the recommended next step is Milestone 2: document download, Docling extraction, quality checks, and structured parsing for a small set of representative BIS documents.

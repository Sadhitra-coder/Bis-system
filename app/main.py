from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from app.config import settings, ensure_directories
from app.database.models import Document, Page, Source
from app.database.session import SessionLocal, init_db

ensure_directories()

app = FastAPI(title=settings.PROJECT_NAME)


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.get("/health")
def health_check():
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as exc:  # pragma: no cover - database may not be running in local dev
        return {"status": "degraded", "database": "unavailable", "error": str(exc)}


@app.get("/api/v1/documents")
def list_documents():
    with SessionLocal() as session:
        documents = session.query(Document).order_by(Document.created_at.desc()).limit(20).all()
        return [
            {
                "id": document.id,
                "title": document.title,
                "document_url": document.document_url,
                "document_type": document.document_type,
                "category": document.category,
                "crawl_status": document.crawl_status,
            }
            for document in documents
        ]


@app.get("/api/v1/documents/{document_id}")
def get_document(document_id: int):
    with SessionLocal() as session:
        document = session.query(Document).filter(Document.id == document_id).first()
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return {
            "id": document.id,
            "title": document.title,
            "document_url": document.document_url,
            "canonical_url": document.canonical_url,
            "document_type": document.document_type,
            "category": document.category,
            "crawl_status": document.crawl_status,
            "parse_status": document.parse_status,
        }


@app.post("/api/v1/crawl")
def trigger_crawl():
    from app.crawler.crawler import BisCrawler

    crawler = BisCrawler()
    result = crawler.crawl(max_pages=20)
    return result


@app.get("/api/v1/documents/{document_id}/status")
def get_document_status(document_id: int):
    with SessionLocal() as session:
        document = session.query(Document).filter(Document.id == document_id).first()
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return {
            "document_id": document.id,
            "crawl_status": document.crawl_status,
            "parse_status": document.parse_status,
            "embedding_status": document.embedding_status,
            "parse_quality_score": document.parse_quality_score,
        }


@app.post("/api/v1/documents/{document_id}/process")
def process_document(document_id: int):
    with SessionLocal() as session:
        document = session.query(Document).filter(Document.id == document_id).first()
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        document.parse_status = "PROCESSING"
        session.commit()
    return {"document_id": document_id, "status": "PROCESSING"}

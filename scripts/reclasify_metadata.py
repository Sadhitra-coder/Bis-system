from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.crawler.metadata import (
    extract_edition,
    extract_keywords,
    extract_revision,
    extract_standard_number,
    infer_category,
    infer_document_type,
    infer_subcategory,
)
from app.database.models import Document
from app.database.session import SessionLocal


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


BATCH_SIZE = 100


def limit_text(value: str | None, max_length: int) -> str | None:
    """
    Prevent PostgreSQL VARCHAR overflow.
    """
    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    return value[:max_length]


def utc_now() -> datetime:
    """
    Return naive UTC datetime compatible with TIMESTAMP WITHOUT TIME ZONE.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def reclassify_document(document: Document) -> None:
    """
    Recalculate metadata for one document using:
      - PDF URL
      - filename
      - document title
      - existing parent page metadata
    """

    document_url = document.document_url or ""

    filename = document_url.rsplit("/", 1)[-1].split("?", 1)[0]

    page_title = ""

    page_category = None
    page_subcategory = None

    # -----------------------------------------------------
    # Use parent Page metadata when available
    # -----------------------------------------------------

    if document.page is not None:
        page_title = document.page.title or ""
        page_category = document.page.category
        page_subcategory = document.page.subcategory

    # -----------------------------------------------------
    # Build text for metadata extraction
    # -----------------------------------------------------

    combined_text = " ".join(
        value
        for value in [
            filename,
            document.title or "",
            page_title,
        ]
        if value
    )

    # -----------------------------------------------------
    # DOCUMENT TYPE
    # -----------------------------------------------------

    document_type = infer_document_type(
        document_url=document_url,
        filename=filename,
        title=" ".join(
            value
            for value in [
                document.title or "",
                page_title,
            ]
            if value
        ),
    )

    # -----------------------------------------------------
    # STANDARD NUMBER
    # -----------------------------------------------------

    standard_number = extract_standard_number(combined_text)

    # -----------------------------------------------------
    # REVISION
    # -----------------------------------------------------

    revision = extract_revision(combined_text)

    # -----------------------------------------------------
    # EDITION
    # -----------------------------------------------------

    edition = extract_edition(combined_text)

    # -----------------------------------------------------
    # CATEGORY
    # -----------------------------------------------------

    category = infer_category(
        combined_text,
        document_type=document_type,
    )

    # If the document itself doesn't give us a useful
    # category, retain the parent page's category.
    if category == "General" and page_category:
        category = page_category

    # -----------------------------------------------------
    # SUBCATEGORY
    # -----------------------------------------------------

    subcategory = infer_subcategory(combined_text)

    # If no subcategory can be extracted from the document,
    # use the parent page's subcategory.
    if not subcategory and page_subcategory:
        subcategory = page_subcategory

    # -----------------------------------------------------
    # KEYWORDS
    # -----------------------------------------------------

    keywords = extract_keywords(
        filename,
        [
            value
            for value in [
                document.title,
                page_title,
            ]
            if value
        ],
    )

    # -----------------------------------------------------
    # SAVE
    # -----------------------------------------------------

    document.document_type = limit_text(
        document_type,
        64,
    )

    document.standard_number = limit_text(
        standard_number,
        64,
    )

    document.revision = limit_text(
        revision,
        64,
    )

    document.edition = limit_text(
        edition,
        64,
    )

    document.category = limit_text(
        category,
        64,
    )

    document.subcategory = limit_text(
        subcategory,
        64,
    )

    document.keywords = limit_text(
        keywords,
        64,
    )

    document.last_crawled_at = utc_now()


def main() -> None:
    session: Session = SessionLocal()

    try:
        total = session.query(Document).count()

        logger.info(
            "Starting metadata reclassification."
        )

        logger.info(
            "Documents found: %s",
            total,
        )

        processed = 0
        failed = 0

        offset = 0

        while True:

            documents = (
                session.query(Document)
                .order_by(Document.id)
                .offset(offset)
                .limit(BATCH_SIZE)
                .all()
            )

            if not documents:
                break

            for document in documents:

                try:
                    reclassify_document(document)

                    processed += 1

                except Exception:
                    failed += 1

                    logger.exception(
                        "Failed to reclassify document ID=%s URL=%s",
                        document.id,
                        document.document_url,
                    )

            # -------------------------------------------------
            # Commit every batch
            # -------------------------------------------------

            try:
                session.commit()

            except Exception:
                session.rollback()

                logger.exception(
                    "Batch commit failed around offset %s",
                    offset,
                )

                failed += len(documents)

            offset += BATCH_SIZE

            logger.info(
                "Progress: %s / %s | failed=%s",
                min(processed, total),
                total,
                failed,
            )

        logger.info("----------------------------------------")
        logger.info("METADATA RECLASSIFICATION COMPLETED")
        logger.info("----------------------------------------")
        logger.info("Total documents : %s", total)
        logger.info("Processed       : %s", processed)
        logger.info("Failed          : %s", failed)

    finally:
        session.close()


if __name__ == "__main__":
    main()
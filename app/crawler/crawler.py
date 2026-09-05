from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.config import settings
from app.crawler.metadata import (
    canonicalize_url,
    extract_edition,
    extract_keywords,
    extract_revision,
    extract_standard_number,
    get_absolute_url,
    infer_category,
    infer_document_type,
    infer_subcategory,
    is_pdf_candidate,
)
from app.database.models import Document, Page, Source
from app.database.session import SessionLocal


logger = logging.getLogger(__name__)


class BisCrawler:
    """
    BIS website metadata crawler.

    Responsibilities:
    - Crawl BIS HTML pages.
    - Discover PDF documents.
    - Store PDF metadata in PostgreSQL.
    - Deduplicate pages and PDFs.
    - Do NOT download or parse PDFs.
    """

    def __init__(
        self,
        base_url: str | None = None,
        session: Session | None = None,
    ):
        self.base_url = (
            base_url or settings.BIS_BASE_URL
        ).rstrip("/")

        self.session = session or SessionLocal()

    # =========================================================
    # SOURCE
    # =========================================================

    def ensure_source(self) -> Source:
        """
        Get or create the BIS source.
        """

        source = (
            self.session.query(Source)
            .filter(Source.name == "BIS")
            .first()
        )

        if source is None:
            source = Source(
                name="BIS",
                base_url=self.base_url,
                source_type="website",
                active=True,
            )

            self.session.add(source)
            self.session.commit()
            self.session.refresh(source)

        return source

    # =========================================================
    # CRAWL
    # =========================================================

    def crawl(
        self,
        seed_urls: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """
        Crawl BIS until the URL queue is completely exhausted.

        PDFs are discovered and stored as metadata.
        PDFs themselves are NOT downloaded.
        """

        seeds = list(
            seed_urls or [self.base_url]
        )

        # -----------------------------------------------------
        # URL QUEUES
        # -----------------------------------------------------

        seen_pages: set[str] = set()
        queued_pages: set[str] = set()

        queue: list[str] = []

        for seed in seeds:
            normalized = canonicalize_url(seed)

            if (
                normalized
                and normalized not in queued_pages
            ):
                queue.append(normalized)
                queued_pages.add(normalized)

        # -----------------------------------------------------
        # STATISTICS
        # -----------------------------------------------------

        pages_crawled = 0
        pdf_links_found = 0
        new_documents = 0
        duplicate_documents = 0
        failed_pages = 0

        source = self.ensure_source()

        # -----------------------------------------------------
        # HTTP CLIENT
        # -----------------------------------------------------

        headers = {
            "User-Agent": (
                "BIS-RAG-Engine/1.0 "
                "(metadata crawler)"
            )
        }

        with httpx.Client(
            timeout=settings.REQUEST_TIMEOUT,
            follow_redirects=True,
            headers=headers,
        ) as client:

            # =================================================
            # CRAWL UNTIL QUEUE IS EMPTY
            # =================================================

            while queue:

                url = queue.pop(0)

                canonical_page = canonicalize_url(url)

                if not canonical_page:
                    continue

                # -------------------------------------------------
                # PAGE DEDUPLICATION
                # -------------------------------------------------

                if canonical_page in seen_pages:
                    continue

                seen_pages.add(canonical_page)

                logger.info(
                    "Crawling page #%s: %s",
                    pages_crawled + 1,
                    canonical_page,
                )

                # =================================================
                # FETCH PAGE
                # =================================================

                try:
                    response = client.get(
                        canonical_page
                    )

                    response.raise_for_status()

                except Exception as exc:
                    failed_pages += 1

                    logger.warning(
                        "Failed to fetch %s: %s",
                        canonical_page,
                        exc,
                    )

                    continue

                # =================================================
                # ONLY PROCESS HTML
                # =================================================

                content_type = (
                    response.headers
                    .get("content-type", "")
                    .lower()
                )

                if not content_type.startswith(
                    "text/html"
                ):
                    logger.debug(
                        "Skipping non-HTML response: %s | %s",
                        canonical_page,
                        content_type,
                    )
                    continue

                # =================================================
                # PARSE HTML
                # =================================================

                soup = BeautifulSoup(
                    response.text,
                    "html.parser",
                )

                # -------------------------------------------------
                # PAGE TITLE
                # -------------------------------------------------

                page_title = ""

                if soup.title:
                    page_title = soup.title.get_text(
                        " ",
                        strip=True,
                    )

                # -------------------------------------------------
                # PAGE TEXT
                # -------------------------------------------------

                page_text = soup.get_text(
                    " ",
                    strip=True,
                )

                # -------------------------------------------------
                # PAGE METADATA
                # -------------------------------------------------

                page_category = infer_category(
                    page_text
                )

                page_subcategory = infer_subcategory(
                    page_text
                )

                # =================================================
                # CREATE / UPDATE PAGE
                # =================================================

                page = (
                    self.session.query(Page)
                    .filter(
                        Page.canonical_url
                        == canonical_page
                    )
                    .first()
                )

                if page is None:

                    page = Page(
                        source_id=source.id,
                        url=canonical_page,
                        canonical_url=canonical_page,
                        title=page_title,
                        page_type="page",
                        parent_url=None,
                        category=page_category,
                        subcategory=page_subcategory,
                        crawl_status="DISCOVERED",
                    )

                    self.session.add(page)
                    self.session.commit()
                    self.session.refresh(page)

                else:

                    page.title = page_title
                    page.category = page_category
                    page.subcategory = page_subcategory
                    page.crawl_status = "DISCOVERED"

                # =================================================
                # PROCESS LINKS
                # =================================================

                for anchor in soup.find_all(
                    "a",
                    href=True,
                ):

                    href = anchor.get("href")

                    candidate = get_absolute_url(
                        canonical_page,
                        href,
                    )

                    if not candidate:
                        continue

                    # -------------------------------------------------
                    # INTERNAL BIS LINKS ONLY
                    # -------------------------------------------------

                    if not self._is_internal(candidate):
                        continue

                    canonical = canonicalize_url(
                        candidate
                    )

                    if not canonical:
                        continue

                    # =================================================
                    # PDF
                    # =================================================

                    if is_pdf_candidate(candidate):

                        pdf_links_found += 1

                        document, created = (
                            self.persist_document(
                                document_url=candidate,
                                page_id=page.id,
                                source_id=source.id,
                                page_title=page_title,
                                page_category=page_category,
                                page_subcategory=page_subcategory,
                            )
                        )

                        if created:
                            new_documents += 1
                        else:
                            duplicate_documents += 1

                        continue

                    # =================================================
                    # HTML PAGE
                    # =================================================

                    if canonical in seen_pages:
                        continue

                    if canonical in queued_pages:
                        continue

                    queue.append(canonical)
                    queued_pages.add(canonical)

                # =================================================
                # FINISH PAGE
                # =================================================

                page.last_crawled_at = (
                    self._utc_now()
                )

                pages_crawled += 1

                self.session.commit()

                logger.info(
                    "Finished page #%s | queue=%s | "
                    "PDF links=%s | new docs=%s",
                    pages_crawled,
                    len(queue),
                    pdf_links_found,
                    new_documents,
                )

        # =====================================================
        # FINAL RESULT
        # =====================================================

        logger.info(
            "Crawl finished: pages=%s pdf_links=%s "
            "new_documents=%s duplicates=%s failures=%s",
            pages_crawled,
            pdf_links_found,
            new_documents,
            duplicate_documents,
            failed_pages,
        )

        return {
            "source_id": source.id,
            "pages_crawled": pages_crawled,
            "pdf_links_found": pdf_links_found,
            "new_documents": new_documents,
            "duplicate_documents": duplicate_documents,
            "failed_pages": failed_pages,
            "seeds": seeds,
        }

    # =========================================================
    # DOCUMENT
    # =========================================================

    def persist_document(
        self,
        document_url: str,
        page_id: int | None = None,
        source_id: int | None = None,
        page_title: str | None = None,
        page_category: str | None = None,
        page_subcategory: str | None = None,
    ) -> tuple[Document | None, bool]:
        """
        Store one unique PDF document.

        Returns:

            (document, True)
                Newly created document.

            (document, False)
                Existing document.

            (None, False)
                Invalid URL.
        """

        canonical = canonicalize_url(
            document_url
        )

        if not canonical:
            return None, False

        # =====================================================
        # CHECK DUPLICATE
        # =====================================================

        existing = (
            self.session.query(Document)
            .filter(
                Document.canonical_url
                == canonical
            )
            .first()
        )

        if existing:

            # -------------------------------------------------
            # Update missing metadata
            # -------------------------------------------------

            if existing.page_id is None:
                existing.page_id = page_id

            if (
                not existing.category
                or existing.category == "General"
            ):
                existing.category = (
                    page_category or "General"
                )

            if (
                not existing.subcategory
                and page_subcategory
            ):
                existing.subcategory = (
                    page_subcategory
                )

            self.session.commit()

            return existing, False

        # =====================================================
        # EXTRACT FILENAME
        # =====================================================

        filename = (
            document_url
            .rsplit("/", 1)[-1]
            .split("?", 1)[0]
        )

        # =====================================================
        # COMBINED TEXT FOR METADATA EXTRACTION
        # =====================================================

        combined_text = " ".join(
            value
            for value in [
                filename,
                page_title,
            ]
            if value
        )

        # =====================================================
        # DOCUMENT TYPE
        # =====================================================

        document_type = infer_document_type(
            document_url,
            filename,
            page_title,
        )

        document_type = self._limit_text(
            document_type,
            64,
        )

        # =====================================================
        # STANDARD NUMBER
        # =====================================================

        standard_number = (
            extract_standard_number(
                combined_text
            )
        )

        standard_number = self._limit_text(
            standard_number,
            64,
        )

        # =====================================================
        # REVISION
        # =====================================================

        revision = extract_revision(
            combined_text
        )

        revision = self._limit_text(
            revision,
            64,
        )

        # =====================================================
        # EDITION
        # =====================================================

        edition = extract_edition(
            combined_text
        )

        edition = self._limit_text(
            edition,
            64,
        )

        # =====================================================
        # CATEGORY
        # =====================================================

        category = infer_category(
            combined_text,
            document_type,
        )

        if category == "General":
            category = (
                page_category or "General"
            )

        category = self._limit_text(
            category,
            64,
        )

        # =====================================================
        # SUBCATEGORY
        # =====================================================

        subcategory = infer_subcategory(
            combined_text
        )

        if not subcategory:
            subcategory = page_subcategory

        subcategory = self._limit_text(
            subcategory,
            64,
        )

        # =====================================================
        # KEYWORDS
        # =====================================================

        keywords = extract_keywords(
            filename,
            [page_title]
            if page_title
            else [],
        )

        keywords = self._limit_text(
            keywords,
            64,
        )

        # =====================================================
        # TITLE
        # =====================================================

        title = self._clean_filename(
            filename
        )

        # =====================================================
        # SAFE FIELD LIMITS
        # =====================================================

        title = self._limit_text(
            title,
            255,
        )

        description = (
            "Indexed from BIS website discovery."
        )

        description = self._limit_text(
            description,
            255,
        )

        language = self._limit_text(
            "en",
            64,
        )

        mime_type = self._limit_text(
            "application/pdf",
            64,
        )

        crawl_status = self._limit_text(
            "DISCOVERED",
            64,
        )

        parse_status = self._limit_text(
            "NOT_PARSED",
            64,
        )

        embedding_status = self._limit_text(
            "NOT_EMBEDDED",
            64,
        )

        # =====================================================
        # CREATE DOCUMENT
        # =====================================================

        document = Document(
            page_id=page_id,
            source_id=source_id,

            title=title,

            document_url=document_url,
            canonical_url=canonical,

            document_type=document_type,

            category=category,
            subcategory=subcategory,

            standard_number=standard_number,
            revision=revision,
            edition=edition,

            description=description,

            keywords=keywords,

            language=language,

            mime_type=mime_type,

            # PDF hasn't been downloaded.
            file_size=0,

            # IMPORTANT:
            # This is deliberately NULL.
            # A real content hash can only be
            # calculated after downloading the PDF.
            content_hash=None,

            crawl_status=crawl_status,

            parse_status=parse_status,

            embedding_status=embedding_status,

            parse_quality_score=0.0,

            last_crawled_at=self._utc_now(),
        )

        try:

            self.session.add(document)
            self.session.commit()
            self.session.refresh(document)

        except Exception:

            self.session.rollback()

            logger.exception(
                "Failed to store document: %s",
                document_url,
            )

            return None, False

        return document, True

    # =========================================================
    # TEXT SAFETY
    # =========================================================

    @staticmethod
    def _limit_text(
        value: str | None,
        max_length: int = 64,
    ) -> str | None:
        """
        Safely limit text to the database column length.

        This prevents PostgreSQL VARCHAR overflow.
        """

        if value is None:
            return None

        value = str(value).strip()

        if not value:
            return None

        return value[:max_length]

    # =========================================================
    # DATETIME
    # =========================================================

    @staticmethod
    def _utc_now() -> datetime:
        """
        Return a timezone-aware UTC datetime converted
        to naive UTC for PostgreSQL TIMESTAMP WITHOUT
        TIME ZONE columns.
        """

        return datetime.now(
            timezone.utc
        ).replace(
            tzinfo=None
        )

    # =========================================================
    # CLEAN FILENAME
    # =========================================================

    @staticmethod
    def _clean_filename(
        filename: str,
    ) -> str:
        """
        Convert a PDF filename into a readable title.
        """

        name = filename

        if name.lower().endswith(".pdf"):
            name = name[:-4]

        # Replace underscores and hyphens.
        name = re.sub(
            r"[_\-]+",
            " ",
            name,
        )

        # Collapse multiple spaces.
        name = re.sub(
            r"\s+",
            " ",
            name,
        )

        return name.strip()

    # =========================================================
    # INTERNAL URL CHECK
    # =========================================================

    @staticmethod
    def _is_internal(
        url: str,
    ) -> bool:
        """
        Allow BIS domains only.
        """

        split = urlsplit(url)

        base_host = urlsplit(
            settings.BIS_BASE_URL
        ).netloc.lower()

        current_host = (
            split.netloc or base_host
        ).lower()

        # Remove port if present.
        current_hostname = (
            current_host.split(":", 1)[0]
        )

        base_hostname = (
            base_host.split(":", 1)[0]
        )

        return (
            not current_hostname
            or current_hostname == base_hostname
            or current_hostname.endswith(
                ".bis.gov.in"
            )
        )

    # =========================================================
    # CLOSE
    # =========================================================

    def close(self) -> None:
        """
        Close database session.
        """

        self.session.close()


# =============================================================
# MAIN
# =============================================================

if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(message)s"
        ),
    )

    crawler = BisCrawler()

    try:

        result = crawler.crawl(
            seed_urls=[
                settings.BIS_BASE_URL
            ],
        )

        print()
        print("=" * 60)
        print("BIS CRAWL COMPLETED")
        print("=" * 60)

        for key, value in result.items():
            print(
                f"{key}: {value}"
            )

        print("=" * 60)

    except KeyboardInterrupt:

        print()
        print("Crawler stopped by user.")

    except Exception as exc:

        logger.exception(
            "Crawler stopped due to an unexpected error: %s",
            exc,
        )

    finally:

        crawler.close()
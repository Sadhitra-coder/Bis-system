from __future__ import annotations

import logging
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.config import settings, RAW_DATA_DIR
from app.database.models import Document
from app.database.session import SessionLocal
from app.documents.cache import (
    calculate_sha256,
    get_cached_pdf_path,
    get_document_directory,
    is_cached,
)


logger = logging.getLogger(__name__)


class DocumentDownloader:
    """
    Downloads BIS PDFs on demand and stores them locally.

    Important:
        This class downloads ONLY the document requested.

    It does NOT crawl BIS.
    It does NOT download all 6,661 documents.
    """

    def __init__(
        self,
        session: Session | None = None,
        raw_data_dir: Path | None = None,
    ) -> None:

        self.session = session or SessionLocal()

        self.raw_data_dir = (
            raw_data_dir
            or RAW_DATA_DIR
        )

        self.raw_data_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.client = httpx.Client(
            timeout=settings.REQUEST_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "BIS-RAG-Engine/1.0 "
                    "(document downloader)"
                )
            },
        )

    # -----------------------------------------------------
    # DOWNLOAD DOCUMENT
    # -----------------------------------------------------

    def download(
        self,
        document_id: int,
        force: bool = False,
    ) -> Path | None:
        """
        Download a single BIS document.

        Returns:
            Path to cached PDF
            None if download failed
        """

        document = (
            self.session
            .query(Document)
            .filter(Document.id == document_id)
            .first()
        )

        if document is None:
            logger.error(
                "Document ID %s not found.",
                document_id,
            )
            return None

        if not document.document_url:
            logger.error(
                "Document ID %s has no URL.",
                document_id,
            )
            return None

        # -------------------------------------------------
        # Check cache
        # -------------------------------------------------

        cached_path = get_cached_pdf_path(
            self.raw_data_dir,
            document_id,
        )

        if not force and is_cached(
            self.raw_data_dir,
            document_id,
        ):
            logger.info(
                "Cache hit: document_id=%s path=%s",
                document_id,
                cached_path,
            )

            # Make sure database status is correct.
            self._update_database_after_download(
                document=document,
                file_path=cached_path,
            )

            return cached_path

        # -------------------------------------------------
        # Mark as processing
        # -------------------------------------------------

        document.crawl_status = "PROCESSING"

        self.session.commit()

        logger.info(
            "Downloading document_id=%s",
            document_id,
        )

        logger.info(
            "URL: %s",
            document.document_url,
        )

        # -------------------------------------------------
        # Prepare paths
        # -------------------------------------------------

        document_directory = get_document_directory(
            self.raw_data_dir,
            document_id,
        )

        temporary_path = (
            document_directory
            / "document.pdf.part"
        )

        final_path = (
            document_directory
            / "document.pdf"
        )

        # -------------------------------------------------
        # Download
        # -------------------------------------------------

        try:

            with self.client.stream(
                "GET",
                document.document_url,
            ) as response:

                response.raise_for_status()

                content_type = (
                    response.headers
                    .get("content-type", "")
                    .lower()
                )

                # -----------------------------------------
                # Content-Type validation
                # -----------------------------------------

                if (
                    content_type
                    and "pdf" not in content_type
                    and "octet-stream" not in content_type
                ):
                    logger.warning(
                        "Unexpected content type for %s: %s",
                        document.document_url,
                        content_type,
                    )

                # -----------------------------------------
                # File size validation
                # -----------------------------------------

                content_length = response.headers.get(
                    "content-length"
                )

                if content_length:

                    try:
                        expected_size = int(
                            content_length
                        )

                        if (
                            expected_size
                            > settings.DOCUMENT_MAX_SIZE
                        ):
                            raise ValueError(
                                "Document exceeds "
                                f"maximum size of "
                                f"{settings.DOCUMENT_MAX_SIZE} bytes."
                            )

                    except ValueError as exc:
                        if "exceeds maximum" in str(exc):
                            raise

                # -----------------------------------------
                # Write file
                # -----------------------------------------

                total_bytes = 0

                with temporary_path.open("wb") as file:

                    for chunk in response.iter_bytes(
                        chunk_size=1024 * 1024
                    ):

                        if not chunk:
                            continue

                        total_bytes += len(chunk)

                        if (
                            total_bytes
                            > settings.DOCUMENT_MAX_SIZE
                        ):
                            raise ValueError(
                                "Downloaded document exceeds "
                                f"maximum size of "
                                f"{settings.DOCUMENT_MAX_SIZE} bytes."
                            )

                        file.write(chunk)

            # ---------------------------------------------
            # Validate downloaded file
            # ---------------------------------------------

            if not temporary_path.exists():
                raise RuntimeError(
                    "Downloaded file does not exist."
                )

            if temporary_path.stat().st_size == 0:
                raise RuntimeError(
                    "Downloaded file is empty."
                )

            # ---------------------------------------------
            # Check PDF signature
            # ---------------------------------------------

            with temporary_path.open("rb") as file:
                header = file.read(5)

            if header != b"%PDF-":
                raise ValueError(
                    "Downloaded file does not appear "
                    "to be a valid PDF."
                )

            # ---------------------------------------------
            # Move into final location
            # ---------------------------------------------

            temporary_path.replace(final_path)

            # ---------------------------------------------
            # Calculate SHA256
            # ---------------------------------------------

            content_hash = calculate_sha256(
                final_path
            )

            # ---------------------------------------------
            # Update PostgreSQL
            # ---------------------------------------------

            document.file_size = final_path.stat().st_size

            document.content_hash = content_hash

            document.crawl_status = "DOWNLOADED"

            self.session.commit()

            logger.info(
                "Download successful:"
            )

            logger.info(
                "  document_id = %s",
                document_id,
            )

            logger.info(
                "  file_size   = %s bytes",
                document.file_size,
            )

            logger.info(
                "  sha256      = %s",
                content_hash,
            )

            logger.info(
                "  path        = %s",
                final_path,
            )

            return final_path

        except Exception as exc:

            logger.exception(
                "Failed to download document_id=%s: %s",
                document_id,
                exc,
            )

            self.session.rollback()

            document = (
                self.session
                .query(Document)
                .filter(Document.id == document_id)
                .first()
            )

            if document:

                document.crawl_status = "FAILED"

                self.session.commit()

            # Remove partial file.
            if temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    logger.warning(
                        "Could not remove partial file: %s",
                        temporary_path,
                    )

            return None

    # -----------------------------------------------------
    # DATABASE UPDATE
    # -----------------------------------------------------

    def _update_database_after_download(
        self,
        document: Document,
        file_path: Path,
    ) -> None:
        """
        Update database metadata for an already cached PDF.
        """

        document.file_size = file_path.stat().st_size

        document.content_hash = calculate_sha256(
            file_path
        )

        document.crawl_status = "DOWNLOADED"

        self.session.commit()

    # -----------------------------------------------------
    # CLOSE
    # -----------------------------------------------------

    def close(self) -> None:
        """
        Close HTTP and database connections.
        """

        self.client.close()

        self.session.close()


# ---------------------------------------------------------
# TEST / MANUAL DOWNLOAD
# ---------------------------------------------------------

if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(message)s"
        ),
    )

    downloader = DocumentDownloader()

    try:

        # ---------------------------------------------
        # CHANGE THIS ID TO A REAL DOCUMENT ID
        # ---------------------------------------------

        DOCUMENT_ID = 385

        path = downloader.download(
            document_id=DOCUMENT_ID
        )

        if path:

            print()
            print(
                "DOCUMENT DOWNLOAD SUCCESSFUL"
            )
            print(
                f"Path: {path}"
            )

        else:

            print()
            print(
                "DOCUMENT DOWNLOAD FAILED"
            )

    finally:

        downloader.close()
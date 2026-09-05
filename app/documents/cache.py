from __future__ import annotations

import hashlib
from pathlib import Path


def calculate_sha256(file_path: Path) -> str:
    """
    Calculate SHA-256 hash of a file.
    """

    sha256 = hashlib.sha256()

    with file_path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            sha256.update(chunk)

    return sha256.hexdigest()


def get_document_directory(
    raw_data_dir: Path,
    document_id: int,
) -> Path:
    """
    Return the cache directory for a document.

    Example:
        data/raw/1234/
    """

    directory = raw_data_dir / str(document_id)

    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    return directory


def get_cached_pdf_path(
    raw_data_dir: Path,
    document_id: int,
) -> Path:
    """
    Return the expected PDF cache path.
    """

    return (
        get_document_directory(
            raw_data_dir,
            document_id,
        )
        / "document.pdf"
    )


def is_cached(
    raw_data_dir: Path,
    document_id: int,
) -> bool:
    """
    Check whether a PDF already exists in cache.
    """

    pdf_path = get_cached_pdf_path(
        raw_data_dir,
        document_id,
    )

    return pdf_path.exists() and pdf_path.stat().st_size > 0
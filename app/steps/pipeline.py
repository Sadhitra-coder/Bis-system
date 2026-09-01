"""
Master BIS Document Processing Pipeline

Processes all PDF files inside:

    data/raw/

including all subfolders.

Pipeline:

    PDF
        ↓
    Extraction
        ↓
    Cleaning
        ↓
    AI Structuring
        ↓
    Markdown Normalization
        ↓
    Chunking
        ↓
    Embedding

The relative folder structure is preserved.
"""

import logging
from pathlib import Path


# ============================================================
# IMPORT PIPELINE STEPS
# ============================================================

from app.steps.extract import extract_pdf
from app.steps.clean import clean_markdown_file
from app.steps.structure import structure_markdown
from app.steps.normalize import normalize_markdown
from app.steps.chunk import create_chunks


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

RAW_DIR = PROJECT_ROOT / "data" / "raw"

MARKDOWN_DIR = PROJECT_ROOT / "data" / "markdown"

CLEANED_DIR = PROJECT_ROOT / "data" / "cleaned"

STRUCTURED_DIR = PROJECT_ROOT / "data" / "structured"

NORMALIZED_DIR = PROJECT_ROOT / "data" / "normalized"

CHUNKS_DIR = PROJECT_ROOT / "data" / "chunks"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
# GET RELATIVE PATH
# ============================================================

def get_relative_path(
    file_path: Path,
    base_dir: Path
) -> Path:
    """
    Returns the path relative to the base directory.

    Example:

    file:
        data/raw/clinical/manual.pdf

    base:
        data/raw

    result:
        clinical/manual.pdf
    """

    return file_path.relative_to(base_dir)


# ============================================================
# STEP 1: EXTRACT PDF
# ============================================================

def run_extraction(
    pdf_path: Path
) -> Path:
    """
    Extract one PDF into Markdown.
    """

    relative_path = get_relative_path(
        pdf_path,
        RAW_DIR
    )

    output_path = (
        MARKDOWN_DIR
        / relative_path.with_suffix(".md")
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    logger.info(
        "STEP 1 | Extracting: %s",
        pdf_path
    )

    # --------------------------------------------------------
    # Import locally because current extract_pdf implementation
    # may use its own output path.
    # --------------------------------------------------------

    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()

    result = converter.convert(
        str(pdf_path)
    )

    markdown = (
        result.document.export_to_markdown()
    )

    output_path.write_text(
        markdown,
        encoding="utf-8"
    )

    logger.info(
        "STEP 1 COMPLETE | %s",
        output_path
    )

    return output_path


# ============================================================
# STEP 2: CLEAN MARKDOWN
# ============================================================

def run_cleaning(
    markdown_path: Path,
    relative_path: Path
) -> Path:
    """
    Clean extracted Markdown.
    """

    output_path = (
        CLEANED_DIR
        / relative_path.with_name(
            relative_path.stem
            + "_cleaned.md"
        )
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    logger.info(
        "STEP 2 | Cleaning: %s",
        markdown_path.name
    )

    clean_markdown_file(
        input_path=str(markdown_path),
        output_path=str(output_path)
    )

    logger.info(
        "STEP 2 COMPLETE | %s",
        output_path
    )

    return output_path


# ============================================================
# STEP 3: STRUCTURE MARKDOWN
# ============================================================

def run_structuring(
    cleaned_path: Path,
    relative_path: Path
) -> Path:
    """
    Structure cleaned Markdown using Groq.
    """

    output_path = (
        STRUCTURED_DIR
        / relative_path.with_name(
            relative_path.stem
            + "_structured.md"
        )
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    logger.info(
        "STEP 3 | Structuring: %s",
        cleaned_path.name
    )

    markdown_content = (
        cleaned_path.read_text(
            encoding="utf-8"
        )
    )

    document_id = (
        relative_path.stem
    )

    structured_markdown = structure_markdown(
        markdown_content=markdown_content,
        document_id=document_id
    )

    output_path.write_text(
        structured_markdown,
        encoding="utf-8"
    )

    logger.info(
        "STEP 3 COMPLETE | %s",
        output_path
    )

    return output_path


# ============================================================
# STEP 4: NORMALIZE MARKDOWN
# ============================================================

def run_normalization(
    structured_path: Path,
    relative_path: Path
) -> Path:
    """
    Normalize Markdown escaping artifacts.
    """

    output_path = (
        NORMALIZED_DIR
        / relative_path.with_name(
            relative_path.stem
            + "_normalized.md"
        )
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    logger.info(
        "STEP 4 | Normalizing: %s",
        structured_path.name
    )

    content = (
        structured_path.read_text(
            encoding="utf-8"
        )
    )

    normalized_content = normalize_markdown(
        content
    )

    output_path.write_text(
        normalized_content,
        encoding="utf-8"
    )

    logger.info(
        "STEP 4 COMPLETE | %s",
        output_path
    )

    return output_path


# ============================================================
# STEP 5: CHUNK DOCUMENT
# ============================================================

def run_chunking(
    normalized_path: Path,
    relative_path: Path
) -> Path:
    """
    Chunk normalized Markdown.
    """

    output_path = (
        CHUNKS_DIR
        / relative_path.with_name(
            relative_path.stem
            + "_chunks.json"
        )
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    logger.info(
        "STEP 5 | Chunking: %s",
        normalized_path.name
    )

    markdown_content = (
        normalized_path.read_text(
            encoding="utf-8"
        )
    )

    document_id = (
        relative_path.stem
    )

    chunks = create_chunks(
        markdown_content=markdown_content,
        document_id=document_id,
        source_file=str(relative_path)
    )

    import json

    output_data = {
        "document_id": document_id,
        "source_file": str(relative_path),
        "total_chunks": len(chunks),
        "chunks": chunks
    }

    output_path.write_text(
        json.dumps(
            output_data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    logger.info(
        "STEP 5 COMPLETE | %s",
        output_path
    )

    logger.info(
        "Chunks created: %s",
        len(chunks)
    )

    return output_path


# ============================================================
# PROCESS ONE PDF
# ============================================================

def process_pdf(
    pdf_path: Path
):
    """
    Run the complete pipeline
    for one PDF.
    """

    logger.info(
        "\n"
        + "=" * 70
    )

    logger.info(
        "PROCESSING DOCUMENT: %s",
        pdf_path
    )

    logger.info(
        "=" * 70
    )

    # --------------------------------------------------------
    # Relative path
    # --------------------------------------------------------

    relative_pdf_path = get_relative_path(
        pdf_path,
        RAW_DIR
    )

    # --------------------------------------------------------
    # STEP 1
    # --------------------------------------------------------

    markdown_path = run_extraction(
        pdf_path
    )

    # --------------------------------------------------------
    # STEP 2
    # --------------------------------------------------------

    cleaned_relative_path = (
        relative_pdf_path.with_name(
            relative_pdf_path.stem
            + "_cleaned.md"
        )
    )

    cleaned_path = run_cleaning(
        markdown_path=markdown_path,
        relative_path=relative_pdf_path
    )

    # --------------------------------------------------------
    # STEP 3
    # --------------------------------------------------------

    structured_relative_path = (
        cleaned_relative_path.with_name(
            cleaned_relative_path.stem
            + "_structured.md"
        )
    )

    structured_path = run_structuring(
        cleaned_path=cleaned_path,
        relative_path=cleaned_relative_path
    )

    # --------------------------------------------------------
    # STEP 4
    # --------------------------------------------------------

    normalized_relative_path = (
        structured_relative_path.with_name(
            structured_relative_path.stem
            + "_normalized.md"
        )
    )

    normalized_path = run_normalization(
        structured_path=structured_path,
        relative_path=structured_relative_path
    )

    # --------------------------------------------------------
    # STEP 5
    # --------------------------------------------------------

    chunks_relative_path = (
        normalized_relative_path
    )

    chunks_path = run_chunking(
        normalized_path=normalized_path,
        relative_path=chunks_relative_path
    )

    logger.info(
        "\nDOCUMENT PIPELINE COMPLETED"
    )

    return {
        "pdf": str(pdf_path),
        "markdown": str(markdown_path),
        "cleaned": str(cleaned_path),
        "structured": str(structured_path),
        "normalized": str(normalized_path),
        "chunks": str(chunks_path)
    }


# ============================================================
# FIND ALL PDFs
# ============================================================

def find_all_pdfs():
    """
    Find every PDF inside data/raw/
    including all subdirectories.
    """

    if not RAW_DIR.exists():

        raise FileNotFoundError(
            f"Raw directory not found: {RAW_DIR}"
        )

    pdf_files = sorted(
        RAW_DIR.rglob("*.pdf")
    )

    return pdf_files


# ============================================================
# RUN COMPLETE PIPELINE
# ============================================================

def run_pipeline():
    """
    Run the pipeline for all PDFs.
    """

    logger.info(
        "\n"
        + "=" * 70
    )

    logger.info(
        "STARTING BIS DOCUMENT PIPELINE"
    )

    logger.info(
        "=" * 70
    )

    pdf_files = find_all_pdfs()

    logger.info(
        "PDF files found: %s",
        len(pdf_files)
    )

    if not pdf_files:

        logger.warning(
            "No PDF files found inside data/raw/"
        )

        return

    successful = []

    failed = []

    # --------------------------------------------------------
    # PROCESS EACH PDF
    # --------------------------------------------------------

    for pdf_path in pdf_files:

        try:

            result = process_pdf(
                pdf_path
            )

            successful.append(
                result
            )

        except Exception as error:

            logger.exception(
                "FAILED: %s",
                pdf_path
            )

            failed.append(
                {
                    "pdf": str(pdf_path),
                    "error": str(error)
                }
            )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    logger.info(
        "\n"
        + "=" * 70
    )

    logger.info(
        "PIPELINE SUMMARY"
    )

    logger.info(
        "=" * 70
    )

    logger.info(
        "Successful documents: %s",
        len(successful)
    )

    logger.info(
        "Failed documents: %s",
        len(failed)
    )

    if failed:

        logger.error(
            "\nFAILED DOCUMENTS:"
        )

        for item in failed:

            logger.error(
                "%s | %s",
                item["pdf"],
                item["error"]
            )

    logger.info(
        "\nPIPELINE FINISHED"
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    run_pipeline()
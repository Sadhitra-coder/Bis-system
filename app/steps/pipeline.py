"""
Master BIS Document Processing Pipeline

Orchestrates the complete document processing workflow:

    PDF (data/raw/**/[all PDFs])
        ↓
    Extraction (Docling)
        ↓ (markdown output)
    Cleaning (lossless formatting)
        ↓ (cleaned output)
    Structuring (Groq AI)
        ↓ (structured output)
    Normalization (artifact removal)
        ↓ (normalized output)
    Chunking (semantic segmentation)
        ↓ (JSON chunks)
    Ready for embedding

ARCHITECTURE:

This is the MASTER ORCHESTRATOR. It:
    - Discovers all PDFs recursively (data/raw/**/[PDFs])
    - Determines relative paths
    - Creates appropriate output directories
    - Calls each step module with correct parameters
    - Handles errors gracefully
    - Logs progress and failures
    - Provides summary report

Each processing step is INDEPENDENT:
    - Processes one file at a time
    - Takes input_path and output_path as parameters
    - Does NOT contain hardcoded directory logic
    - Creates output directories as needed

DIRECTORY STRUCTURE PRESERVED:

Input:
    data/raw/clinical/thermometer/manual.pdf

Outputs:
    data/markdown/clinical/thermometer/manual.md
    data/cleaned/clinical/thermometer/manual_cleaned.md
    data/structured/clinical/thermometer/manual_structured.md
    data/normalized/clinical/thermometer/manual_normalized.md
    data/chunks/clinical/thermometer/manual_chunks.json
"""

import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Import processing step modules
from app.steps.extract import extract_pdf
from app.steps.clean import clean_markdown_file
from app.steps.structure import structure_markdown_file
from app.steps.normalize import normalize_markdown_file
from app.steps.chunk import chunk_markdown_file


# ============================================================
# CONFIGURATION
# ============================================================

# Safely determine PROJECT_ROOT
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Define directory structure
RAW_DIR = PROJECT_ROOT / "data" / "raw"
MARKDOWN_DIR = PROJECT_ROOT / "data" / "markdown"
CLEANED_DIR = PROJECT_ROOT / "data" / "cleaned"
STRUCTURED_DIR = PROJECT_ROOT / "data" / "structured"
NORMALIZED_DIR = PROJECT_ROOT / "data" / "normalized"
CHUNKS_DIR = PROJECT_ROOT / "data" / "chunks"


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# PIPELINE STATE
# ============================================================

class PipelineState:
    """Tracks pipeline execution state."""

    def __init__(self):
        self.successful = []
        self.failed = []
        self.skipped = []

    def add_success(self, pdf_path: Path, results: Dict):
        """Record successful PDF processing."""
        self.successful.append({
            "pdf": str(pdf_path),
            "results": results
        })

    def add_failure(self, pdf_path: Path, error: Exception):
        """Record failed PDF processing."""
        self.failed.append({
            "pdf": str(pdf_path),
            "error": str(error)
        })

    def add_skip(self, pdf_path: Path, reason: str):
        """Record skipped PDF."""
        self.skipped.append({
            "pdf": str(pdf_path),
            "reason": reason
        })

    def summary(self) -> str:
        """Generate summary report."""
        lines = [
            "\n" + "=" * 70,
            "PIPELINE SUMMARY",
            "=" * 70,
            f"Total PDFs discovered: {len(self.successful) + len(self.failed) + len(self.skipped)}",
            f"Successfully processed: {len(self.successful)}",
            f"Failed: {len(self.failed)}",
            f"Skipped: {len(self.skipped)}",
        ]

        if self.failed:
            lines.append("\nFAILED PDFs:")
            for item in self.failed:
                lines.append(f"  - {item['pdf']}: {item['error']}")

        if self.skipped:
            lines.append("\nSKIPPED PDFs:")
            for item in self.skipped:
                lines.append(f"  - {item['pdf']}: {item['reason']}")

        lines.append("=" * 70 + "\n")

        return "\n".join(lines)


# ============================================================
# FILENAME STRATEGY
# ============================================================

def get_output_stem(
    relative_path: Path,
    step_name: str
) -> str:
    """
    Generate output filename stem for each step.

    This ensures clean, deterministic filenames without
    suffix accumulation (e.g., no _cleaned_structured_normalized).

    Each step's output has a clean single suffix:
        - extract: {name}.md
        - clean: {name}_cleaned.md
        - structure: {name}_structured.md
        - normalize: {name}_normalized.md
        - chunk: {name}_chunks.json

    Args:
        relative_path: Path relative to data/raw/
        step_name: Step identifier (extract, clean, structure, normalize, chunk)

    Returns:
        Clean output filename stem
    """

    # Get the original name (without .pdf)
    base_name = relative_path.stem

    if step_name == "extract":
        return base_name

    elif step_name == "clean":
        return f"{base_name}_cleaned"

    elif step_name == "structure":
        return f"{base_name}_structured"

    elif step_name == "normalize":
        return f"{base_name}_normalized"

    elif step_name == "chunk":
        return f"{base_name}_chunks"

    else:
        return base_name


# ============================================================
# STEP EXECUTION
# ============================================================

def run_extraction(
    pdf_path: Path,
    relative_path: Path
) -> Path:
    """
    STEP 1: Extract PDF to Markdown.

    Args:
        pdf_path: Full path to PDF.
        relative_path: Relative path from data/raw/.

    Returns:
        Path to generated Markdown file.
    """

    output_file = (
        MARKDOWN_DIR
        / relative_path.parent
        / (get_output_stem(relative_path, "extract") + ".md")
    )

    output_file.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "STEP 1 EXTRACT | input=%s | output=%s",
        pdf_path.name,
        output_file.name
    )

    extract_pdf(pdf_path, output_file)

    return output_file


def run_cleaning(
    markdown_path: Path,
    relative_path: Path
) -> Path:
    """
    STEP 2: Clean extracted Markdown.

    Args:
        markdown_path: Path to raw Markdown.
        relative_path: Relative path from data/raw/.

    Returns:
        Path to cleaned Markdown file.
    """

    output_file = (
        CLEANED_DIR
        / relative_path.parent
        / (get_output_stem(relative_path, "clean") + ".md")
    )

    output_file.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "STEP 2 CLEAN | input=%s | output=%s",
        markdown_path.name,
        output_file.name
    )

    clean_markdown_file(markdown_path, output_file)

    return output_file


def run_structuring(
    cleaned_path: Path,
    relative_path: Path
) -> Path:
    """
    STEP 3: Structure Markdown using Groq.

    Args:
        cleaned_path: Path to cleaned Markdown.
        relative_path: Relative path from data/raw/.

    Returns:
        Path to structured Markdown file.
    """

    output_file = (
        STRUCTURED_DIR
        / relative_path.parent
        / (get_output_stem(relative_path, "structure") + ".md")
    )

    output_file.parent.mkdir(parents=True, exist_ok=True)

    document_id = relative_path.stem

    logger.info(
        "STEP 3 STRUCTURE | input=%s | output=%s",
        cleaned_path.name,
        output_file.name
    )

    structure_markdown_file(
        cleaned_path,
        output_file,
        document_id=document_id
    )

    return output_file


def run_normalization(
    structured_path: Path,
    relative_path: Path
) -> Path:
    """
    STEP 4: Normalize Markdown escaping.

    Args:
        structured_path: Path to structured Markdown.
        relative_path: Relative path from data/raw/.

    Returns:
        Path to normalized Markdown file.
    """

    output_file = (
        NORMALIZED_DIR
        / relative_path.parent
        / (get_output_stem(relative_path, "normalize") + ".md")
    )

    output_file.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "STEP 4 NORMALIZE | input=%s | output=%s",
        structured_path.name,
        output_file.name
    )

    normalize_markdown_file(structured_path, output_file)

    return output_file


def run_chunking(
    normalized_path: Path,
    relative_path: Path
) -> Path:
    """
    STEP 5: Chunk normalized Markdown.

    Args:
        normalized_path: Path to normalized Markdown.
        relative_path: Relative path from data/raw/.

    Returns:
        Path to chunk JSON file.
    """

    output_file = (
        CHUNKS_DIR
        / relative_path.parent
        / (get_output_stem(relative_path, "chunk") + ".json")
    )

    output_file.parent.mkdir(parents=True, exist_ok=True)

    document_id = relative_path.stem
    source_file = str(relative_path)

    logger.info(
        "STEP 5 CHUNK | input=%s | output=%s",
        normalized_path.name,
        output_file.name
    )

    chunk_markdown_file(
        normalized_path,
        output_file,
        document_id=document_id,
        source_file=source_file
    )

    return output_file


# ============================================================
# PROCESS ONE PDF
# ============================================================

def process_pdf(pdf_path: Path) -> Dict[str, Path]:
    """
    Run complete pipeline for one PDF.

    Args:
        pdf_path: Full path to PDF file.

    Returns:
        Dictionary with paths to all outputs.

    Raises:
        Exception: If any step fails (caller handles it).
    """

    # Get relative path for directory structure preservation
    relative_pdf_path = pdf_path.relative_to(RAW_DIR)

    logger.info(
        "\n" + "=" * 70
    )

    logger.info(
        "PROCESSING PDF: %s",
        relative_pdf_path
    )

    logger.info(
        "=" * 70
    )

    # STEP 1: Extract
    markdown_path = run_extraction(pdf_path, relative_pdf_path)

    # STEP 2: Clean
    cleaned_path = run_cleaning(markdown_path, relative_pdf_path)

    # STEP 3: Structure
    structured_path = run_structuring(cleaned_path, relative_pdf_path)

    # STEP 4: Normalize
    normalized_path = run_normalization(structured_path, relative_pdf_path)

    # STEP 5: Chunk
    chunks_path = run_chunking(normalized_path, relative_pdf_path)

    logger.info("PIPELINE COMPLETE FOR: %s", relative_pdf_path)

    return {
        "pdf": pdf_path,
        "markdown": markdown_path,
        "cleaned": cleaned_path,
        "structured": structured_path,
        "normalized": normalized_path,
        "chunks": chunks_path
    }


# ============================================================
# FIND PDFs
# ============================================================

def find_all_pdfs() -> List[Path]:
    """
    Discover all PDF files recursively in data/raw/.

    Returns:
        Sorted list of PDF paths.
    """

    if not RAW_DIR.exists():
        raise FileNotFoundError(
            f"Raw data directory not found: {RAW_DIR}"
        )

    pdf_files = sorted(RAW_DIR.rglob("*.pdf"))

    return pdf_files


# ============================================================
# MAIN PIPELINE
# ============================================================

def run_pipeline():
    """
    Main pipeline orchestrator.

    Discovers all PDFs and processes them sequentially,
    capturing errors and providing summary.
    """

    logger.info(
        "\n" + "=" * 70
    )

    logger.info(
        "STARTING BIS DOCUMENT PROCESSING PIPELINE"
    )

    logger.info(
        "=" * 70
    )

    # Discover PDFs
    try:
        pdf_files = find_all_pdfs()
    except FileNotFoundError as error:
        logger.error("Cannot start pipeline: %s", error)
        return

    logger.info("PDFs discovered: %s", len(pdf_files))

    if not pdf_files:
        logger.warning("No PDFs found in %s", RAW_DIR)
        return

    # Process PDFs
    state = PipelineState()

    for pdf_path in pdf_files:

        try:

            results = process_pdf(pdf_path)
            state.add_success(pdf_path, results)

        except Exception as error:

            logger.exception(
                "FAILED TO PROCESS: %s",
                pdf_path
            )

            state.add_failure(pdf_path, error)

    # Print summary
    logger.info(state.summary())

    # Exit with appropriate code
    if state.failed:
        sys.exit(1)
    else:
        sys.exit(0)


# ============================================================
# ENTRY POINT

if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )

    run_pipeline()

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
    Embedding + vector indexing (BGE-large -> ChromaDB)

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
    -> indexed into data/vector_db (Chroma collection)

LIBRARY SAFETY:

run_pipeline() returns a PipelineResult. It never calls
sys.exit(), so the API layer and the tests can call it
directly. Only the __main__ block translates the result into a
process exit code.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import (
    CHUNKS_DATA_DIR,
    CLEANED_DATA_DIR,
    MARKDOWN_DATA_DIR,
    NORMALIZED_DATA_DIR,
    RAW_DATA_DIR,
    STRUCTURED_DATA_DIR,
    ensure_directories,
)

# Import processing step modules
from app.steps.extract import extract_pdf
from app.steps.clean import clean_markdown_file
from app.steps.structure import structure_markdown_file
from app.steps.normalize import normalize_markdown_file
from app.steps.chunk import chunk_markdown_file
from app.steps.embed import embed_chunk_file
from app.steps.bis_extractor import build_document_metadata, derive_document_id
from app.steps.registry import compute_file_hash
from app.steps.provenance import validate_chunks, provenance_quality_report


# ============================================================
# CONFIGURATION
#
# Directory layout is owned by app.config - this module must
# not redefine it.
# ============================================================

RAW_DIR = RAW_DATA_DIR
MARKDOWN_DIR = MARKDOWN_DATA_DIR
CLEANED_DIR = CLEANED_DATA_DIR
STRUCTURED_DIR = STRUCTURED_DATA_DIR
NORMALIZED_DIR = NORMALIZED_DATA_DIR
CHUNKS_DIR = CHUNKS_DATA_DIR


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# PIPELINE STATE
# ============================================================

class PipelineState:
    """
    Tracks pipeline execution state.

    This is the structured result object returned by
    run_pipeline(). Callers inspect `ok`, `failed` and
    `to_dict()` instead of relying on a process exit code.
    """

    def __init__(self):
        self.successful = []
        self.failed = []
        self.skipped = []

    # --------------------------------------------------------
    # RESULT ACCESSORS
    # --------------------------------------------------------

    @property
    def ok(self) -> bool:
        """True when nothing failed."""
        return not self.failed

    @property
    def total(self) -> int:
        return (
            len(self.successful)
            + len(self.failed)
            + len(self.skipped)
        )

    @property
    def chunks_indexed(self) -> int:
        """Total chunks written to the vector index."""
        return sum(
            item["results"].get("chunks_indexed", 0)
            for item in self.successful
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        JSON-serialisable view, suitable for an API response.
        """
        return {
            "ok": self.ok,
            "total": self.total,
            "processed": len(self.successful),
            "failed": len(self.failed),
            "skipped": len(self.skipped),
            "chunks_indexed": self.chunks_indexed,
            "successful": [
                {
                    "pdf": item["pdf"],
                    "chunks_indexed": item["results"].get(
                        "chunks_indexed", 0
                    ),
                    "outputs": {
                        key: str(value)
                        for key, value in item["results"].items()
                        if isinstance(value, Path)
                    }
                }
                for item in self.successful
            ],
            "failures": list(self.failed),
            "skips": list(self.skipped)
        }

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
            f"Total PDFs discovered: {self.total}",
            f"Successfully processed: {len(self.successful)}",
            f"Failed: {len(self.failed)}",
            f"Skipped: {len(self.skipped)}",
            f"Chunks indexed: {self.chunks_indexed}",
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
    relative_path: Path,
    doc_metadata: Optional[Any] = None
) -> Path:
    """
    STEP 5: Create semantic chunks from normalized Markdown.

    Args:
        normalized_path: Path to normalized Markdown file.
        relative_path: Relative path from data/raw/.
        doc_metadata: Optional DocumentMetadata instance.

    Returns:
        Path to chunk JSON file.
    """

    output_file = (
        CHUNKS_DIR
        / relative_path.parent
        / (get_output_stem(relative_path, "chunk") + ".json")
    )

    output_file.parent.mkdir(parents=True, exist_ok=True)

    document_id = getattr(doc_metadata, "document_id", None) or relative_path.stem
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
        source_file=source_file,
        doc_metadata=doc_metadata
    )

    return output_file


def run_embedding(
    chunks_path: Path,
    relative_path: Path,
    model: Optional[Any] = None,
    collection: Optional[Any] = None
) -> int:
    """
    STEP 6: Embed chunks and index them into ChromaDB.

    This is the stage that makes an ingested document actually
    retrievable. Without it, chunk JSON on disk is inert.

    Args:
        chunks_path: Path to the chunk JSON file.
        relative_path: Relative path from data/raw/.
        model: Preloaded SentenceTransformer, shared across
               PDFs so BGE-large is loaded once per run.
        collection: Preloaded Chroma collection, likewise
                    shared.

    Returns:
        Number of chunks indexed.
    """

    logger.info(
        "STEP 6 EMBED | input=%s",
        chunks_path.name
    )

    return embed_chunk_file(
        chunks_path,
        model=model,
        collection=collection
    )


# ============================================================
# PROCESS ONE PDF
# ============================================================

def process_pdf(
    pdf_path: Path,
    embedding_model: Optional[Any] = None,
    collection: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Run complete pipeline for one PDF.

    Args:
        pdf_path: Full path to PDF file.
        embedding_model: Preloaded SentenceTransformer, so the
                         1.3 GB BGE-large model is loaded once
                         per run instead of once per PDF.
        collection: Preloaded Chroma collection.

    Returns:
        Dictionary with paths to all outputs plus the number of
        chunks indexed.

    Raises:
        Exception: If any step fails (caller handles it).
    """

    # Get relative path for directory structure preservation
    pdf_path = Path(pdf_path).resolve()
    raw_dir = RAW_DIR.resolve()
    try:
        relative_pdf_path = pdf_path.relative_to(raw_dir)
    except ValueError:
        relative_pdf_path = Path(pdf_path.name)

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

    # Document-level provenance metadata
    source_hash = compute_file_hash(pdf_path)
    document_id = derive_document_id(source_hash)
    try:
        markdown_sample = markdown_path.read_text(encoding="utf-8")[:4000]
    except Exception:
        markdown_sample = ""
    doc_metadata = build_document_metadata(
        source_file=str(relative_pdf_path),
        source_filename=pdf_path.name,
        source_hash=source_hash,
        text_sample=markdown_sample,
        document_id=document_id,
    )

    # STEP 2: Clean
    cleaned_path = run_cleaning(markdown_path, relative_pdf_path)

    # STEP 3: Structure
    structured_path = run_structuring(cleaned_path, relative_pdf_path)

    # STEP 4: Normalize
    normalized_path = run_normalization(structured_path, relative_pdf_path)

    # STEP 5: Chunk
    chunks_path = run_chunking(normalized_path, relative_pdf_path, doc_metadata=doc_metadata)

    # Provenance validation and quality report
    try:
        import json
        with open(chunks_path, "r", encoding="utf-8") as f:
            chunk_data = json.load(f)
        chunks_list = chunk_data.get("chunks", [])
        prov_issues = validate_chunks(chunks_list)
        if prov_issues:
            logger.warning(
                "Provenance issues detected in %d chunks for %s",
                len(prov_issues),
                relative_pdf_path
            )
        prov_quality = provenance_quality_report(chunks_list)
        logger.info("Provenance quality for %s: %s", relative_pdf_path, prov_quality)
    except Exception as e:
        logger.warning("Could not compute provenance report: %s", e)
        prov_issues = {}
        prov_quality = {}

    # STEP 5.5: Build BIS Knowledge Model (Standard -> Version -> Part -> Clause -> References)
    try:
        from app.knowledge import default_knowledge_service
        knowledge_diag = default_knowledge_service.build_knowledge_from_ingestion(
            doc_metadata=doc_metadata,
            chunks=chunks_list
        )
        knowledge_report = knowledge_diag.model_dump()
    except Exception as e:
        logger.warning("Could not build BIS knowledge graph: %s", e)
        knowledge_report = {}

    # STEP 6: Embed + index
    chunks_indexed = run_embedding(
        chunks_path,
        relative_pdf_path,
        model=embedding_model,
        collection=collection
    )

    logger.info(
        "PIPELINE COMPLETE FOR: %s (%d chunks indexed)",
        relative_pdf_path,
        chunks_indexed
    )

    return {
        "pdf": pdf_path,
        "markdown": markdown_path,
        "cleaned": cleaned_path,
        "structured": structured_path,
        "normalized": normalized_path,
        "chunks": chunks_path,
        "chunks_indexed": chunks_indexed,
        "document_id": document_id,
        "doc_metadata": doc_metadata.model_dump(),
        "provenance_quality": prov_quality,
        "provenance_issues": prov_issues,
        "knowledge_diagnostics": knowledge_report,
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

def run_pipeline(
    pdf_paths: Optional[List[Path]] = None,
    embedding_model: Optional[Any] = None,
    collection: Optional[Any] = None
) -> PipelineState:
    """
    Main pipeline orchestrator.

    Library-safe: returns a PipelineState instead of calling
    sys.exit(), so this can be invoked from the API layer, from
    tests, or from another script.

    Parameters
    ----------
    pdf_paths:
        Explicit list of PDFs to process. When None, every PDF
        under data/raw/ is discovered recursively.

    embedding_model:
        Preloaded SentenceTransformer. Passed straight through
        to every PDF so BGE-large is loaded at most once per
        run. When None, embed_chunk_file loads it on first use
        and that instance is then reused for the remaining PDFs.

    collection:
        Preloaded Chroma collection, same reasoning.

    Returns
    -------
    PipelineState
        Always returned, including when discovery fails. Check
        `.ok` and `.failed`.
    """

    logger.info("=" * 70)
    logger.info("STARTING BIS DOCUMENT PROCESSING PIPELINE")
    logger.info("=" * 70)

    ensure_directories()

    state = PipelineState()

    # ---------------- Discover PDFs ----------------

    if pdf_paths is None:

        try:
            pdf_files = find_all_pdfs()
        except FileNotFoundError as error:
            logger.error("Cannot start pipeline: %s", error)
            state.add_failure(RAW_DIR, error)
            return state

    else:
        pdf_files = [Path(p) for p in pdf_paths]

    logger.info("PDFs discovered: %s", len(pdf_files))

    if not pdf_files:
        logger.warning("No PDFs found in %s", RAW_DIR)
        return state

    # ---------------- Process PDFs ----------------
    #
    # The model / collection are loaded lazily on the first PDF
    # and then reused, so a 50-PDF run pays the BGE-large load
    # cost once rather than 50 times.

    for pdf_path in pdf_files:

        try:

            if embedding_model is None or collection is None:

                from app.steps.embed import (
                    get_collection,
                    load_embedding_model,
                )

                if embedding_model is None:
                    embedding_model = load_embedding_model()

                if collection is None:
                    collection = get_collection()

            results = process_pdf(
                pdf_path,
                embedding_model=embedding_model,
                collection=collection
            )

            state.add_success(pdf_path, results)

        except Exception as error:

            logger.exception(
                "FAILED TO PROCESS: %s",
                pdf_path
            )

            state.add_failure(pdf_path, error)

    logger.info(state.summary())

    return state


# ============================================================
# ENTRY POINT
# ============================================================

def main() -> int:
    """
    CLI wrapper. Translating the result into an exit code is
    the CLI's job, not run_pipeline()'s.
    """

    state = run_pipeline()

    return 0 if state.ok else 1


if __name__ == "__main__":

    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    sys.exit(main())

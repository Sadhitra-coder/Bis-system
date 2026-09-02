"""
Step 1: PDF Extraction

Input:
    Raw PDF file path

Process:
    Uses Docling to extract PDF into Markdown.
    No cleaning or restructuring.

Output:
    Raw Markdown content

This module is designed to be generic and reusable:
    - Does NOT contain hardcoded paths
    - Processes ONE PDF at a time
    - Takes input_path and output_path as parameters
    - Creates parent directories if needed
"""

import logging
from pathlib import Path

from docling.document_converter import DocumentConverter


logger = logging.getLogger(__name__)


class ExtractorError(Exception):
    """Raised when PDF extraction fails."""


def extract_pdf(
    input_path: Path,
    output_path: Path
) -> Path:
    """
    Extract PDF using Docling and save raw Markdown.

    This function:
        - Processes exactly one PDF
        - Creates output parent directory if needed
        - Saves extracted Markdown to output_path
        - Returns the output_path

    Args:
        input_path: Path to the PDF file.
        output_path: Path where Markdown will be saved.

    Returns:
        Path to the saved Markdown file.

    Raises:
        ExtractorError: If extraction fails.
        FileNotFoundError: If input PDF not found.
    """

    input_path = Path(input_path)
    output_path = Path(output_path)

    # Validate input
    if not input_path.exists():
        raise FileNotFoundError(
            f"PDF not found: {input_path}"
        )

    if input_path.suffix.lower() != ".pdf":
        raise ValueError(
            f"Input file must be a PDF, got: {input_path.suffix}"
        )

    # Create output directory
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    logger.info(
        "Extracting PDF | input=%s | output=%s",
        input_path.name,
        output_path.name
    )

    try:

        # Docling extraction
        converter = DocumentConverter()
        result = converter.convert(str(input_path))

        # Convert to Markdown
        markdown = result.document.export_to_markdown()

        if not markdown.strip():
            raise ExtractorError(
                f"Extraction produced empty Markdown from {input_path.name}"
            )

        # Save Markdown
        output_path.write_text(
            markdown,
            encoding="utf-8"
        )

        logger.info(
            "Extraction successful | "
            "file=%s | characters=%s",
            output_path.name,
            len(markdown)
        )

        return output_path

    except ExtractorError:
        raise

    except Exception as error:
        logger.exception(
            "Extraction failed | file=%s",
            input_path.name
        )
        raise ExtractorError(
            f"Failed to extract PDF '{input_path.name}': {str(error)}"
        ) from error
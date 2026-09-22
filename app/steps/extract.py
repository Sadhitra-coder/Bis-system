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

import os
import logging
from pathlib import Path

# On Windows without Developer Mode, huggingface symlinks fail with WinError 1314.
# Disabling symlink detection forces copy/move fallback.
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
try:
    import huggingface_hub.file_download
    huggingface_hub.file_download.are_symlinks_supported = lambda *args, **kwargs: False
except Exception:
    pass

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

        # Docling extraction with pypdf fallback
        markdown = ""
        try:
            converter = DocumentConverter()
            result = converter.convert(str(input_path))
            doc = result.document

            page_markdowns = []
            if hasattr(doc, "pages") and doc.pages:
                for page_no in sorted(doc.pages.keys()):
                    p_md = doc.export_to_markdown(page_no=page_no)
                    if p_md.strip():
                        page_markdowns.append(f"<!-- PAGE {page_no} -->\n\n" + p_md.strip())

            if not page_markdowns:
                raw_md = doc.export_to_markdown()
                if raw_md.strip():
                    page_markdowns.append("<!-- PAGE 1 -->\n\n" + raw_md.strip())

            markdown = "\n\n".join(page_markdowns)
        except Exception as docling_err:
            logger.warning("Docling extraction unavailable (%s); using pypdf offline fallback.", docling_err)
            import pypdf
            reader = pypdf.PdfReader(str(input_path))
            page_markdowns = []
            for page_idx, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    page_markdowns.append(f"<!-- PAGE {page_idx} -->\n\n{text.strip()}")
            markdown = "\n\n".join(page_markdowns)

        if not markdown.strip():
            raise ExtractorError(
                f"Extraction produced empty Markdown from {input_path.name}"
            )

        # Save Markdown
        output_path.write_text(
            markdown,
            encoding="utf-8"
        )

        # Preserve structural provenance sidecar (page count, bounding boxes)
        try:
            prov_data = {
                "document": input_path.name,
                "page_count": len(doc.pages) if hasattr(doc, "pages") and doc.pages else 1,
                "items": []
            }
            for item, _ in doc.iterate_items():
                prov = getattr(item, "prov", None)
                if prov:
                    p0 = prov[0]
                    bbox = getattr(p0, "bbox", None)
                    prov_data["items"].append({
                        "type": type(item).__name__,
                        "page_no": getattr(p0, "page_no", None),
                        "bbox": {
                            "l": getattr(bbox, "l", 0.0),
                            "t": getattr(bbox, "t", 0.0),
                            "r": getattr(bbox, "r", 0.0),
                            "b": getattr(bbox, "b", 0.0),
                        } if bbox else None
                    })
            prov_path = output_path.with_suffix(".prov.json")
            import json
            prov_path.write_text(json.dumps(prov_data, indent=2), encoding="utf-8")
        except Exception as prov_err:
            logger.debug("Failed to record provenance sidecar: %s", prov_err)

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
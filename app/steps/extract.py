"""
Step 1: PDF Extraction

PDF
 ↓
Docling
 ↓
Raw Markdown saved in data/markdown/

No cleaning.
No normalization.
No restructuring.
"""

from pathlib import Path

from docling.document_converter import DocumentConverter


def extract_pdf(pdf_path: str) -> str:
    """
    Extract PDF using Docling and save raw Markdown.

    Returns:
        Path of the saved Markdown file.
    """

    # Input PDF
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # Output folder
    output_dir = Path("data/markdown")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Output Markdown filename
    output_path = output_dir / f"{pdf_path.stem}.md"

    print(f"Extracting: {pdf_path.name}")

    # Docling extraction
    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))

    # Convert to Markdown
    markdown = result.document.export_to_markdown()

    # Save Markdown
    output_path.write_text(
        markdown,
        encoding="utf-8"
    )

    print(f"Saved Markdown: {output_path}")
    print(f"Characters extracted: {len(markdown)}")

    return str(output_path)


if __name__ == "__main__":

    extract_pdf(
        "data/raw/Product-Manual-30551-V2.pdf"
    )
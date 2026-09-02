"""
Step 2: Lossless Markdown Cleaning

Input:
    Raw Markdown extracted by Docling

Process:
    Performs ONLY safe formatting cleanup.
    Preserves all document information.

Output:
    Cleaned Markdown

This module is designed to be generic and reusable:
    - Does NOT contain hardcoded paths
    - Processes ONE Markdown file at a time
    - Takes input_path and output_path as parameters
    - Creates parent directories if needed

Safe operations:
    - Decode HTML entities
    - Normalize Unicode whitespace
    - Normalize line endings
    - Remove trailing whitespace
    - Collapse excessive blank lines
    - Remove zero-width characters

Unsafe operations NOT performed:
    - Removing duplicate table cells
    - Removing repeated text
    - Merging table columns
    - Merging rows
    - Fixing table structure
    - Inferring headings
    - Removing empty table cells
    - Removing symbols or punctuation
    - Rewriting sentences
"""

import html
import logging
import re
from pathlib import Path
from typing import Any, Dict


logger = logging.getLogger(__name__)


class MarkdownCleaningError(Exception):
    """Raised when Markdown cleaning fails."""


def clean_markdown(markdown: str) -> str:
    """
    Perform lossless formatting cleanup on Markdown.

    This function preserves all document information.

    Args:
        markdown: Raw Markdown content.

    Returns:
        Cleaned Markdown content.
    """

    if not isinstance(markdown, str):
        raise TypeError("markdown must be a string.")

    if not markdown:
        return markdown

    original_length = len(markdown)

    # --------------------------------------------------
    # 1. Normalize line endings
    #
    # Windows: \r\n
    # Old Mac: \r
    # Unix:    \n
    # --------------------------------------------------

    cleaned = markdown.replace("\r\n", "\n")
    cleaned = cleaned.replace("\r", "\n")

    # --------------------------------------------------
    # 2. Decode HTML entities
    #
    # Example: &amp; -> &, &nbsp; -> non-breaking space
    # --------------------------------------------------

    cleaned = html.unescape(cleaned)

    # --------------------------------------------------
    # 3. Normalize special spaces
    #
    # Replace Unicode space variants with regular spaces.
    # No words or information are removed.
    # --------------------------------------------------

    special_spaces = [
        "\u00A0",  # Non-breaking space
        "\u2000",  # En quad
        "\u2001",  # Em quad
        "\u2002",  # En space
        "\u2003",  # Em space
        "\u2004",  # Three-per-em space
        "\u2005",  # Four-per-em space
        "\u2006",  # Six-per-em space
        "\u2007",  # Figure space
        "\u2008",  # Punctuation space
        "\u2009",  # Thin space
        "\u200A",  # Hair space
        "\u202F",  # Narrow non-breaking space
        "\u205F",  # Medium mathematical space
        "\u3000",  # Ideographic space
    ]

    for space in special_spaces:
        cleaned = cleaned.replace(space, " ")

    # --------------------------------------------------
    # 4. Remove zero-width formatting characters
    #
    # These are invisible formatting artifacts
    # that do not represent document information.
    # --------------------------------------------------

    zero_width_chars = [
        "\u200B",  # Zero-width space
        "\u200C",  # Zero-width non-joiner
        "\u200D",  # Zero-width joiner
        "\uFEFF",  # Byte-order mark
    ]

    for char in zero_width_chars:
        cleaned = cleaned.replace(char, "")

    # --------------------------------------------------
    # 5. Remove trailing whitespace
    #
    # Removes trailing spaces/tabs from each line.
    # Internal spaces and table structure are preserved.
    # --------------------------------------------------

    lines = cleaned.split("\n")
    cleaned_lines = [line.rstrip() for line in lines]
    cleaned = "\n".join(cleaned_lines)

    # --------------------------------------------------
    # 6. Limit excessive consecutive blank lines
    #
    # Replace 4+ blank lines with 3 blank lines.
    # Paragraph separation is preserved.
    # No textual content is removed.
    # --------------------------------------------------

    cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned)

    # --------------------------------------------------
    # Final validation
    # --------------------------------------------------

    if not cleaned.strip():
        raise MarkdownCleaningError(
            "Cleaning produced empty Markdown."
        )

    logger.info(
        "Markdown cleaning completed | "
        "original_chars=%s | cleaned_chars=%s",
        original_length,
        len(cleaned),
    )

    return cleaned


def clean_markdown_file(
    input_path: Path,
    output_path: Path
) -> Dict[str, Any]:
    """
    Read a Markdown file, clean it safely, and save the result.

    This function:
        - Processes exactly one Markdown file
        - Creates output parent directory if needed
        - Saves cleaned Markdown to output_path
        - Returns metadata about the cleaning

    Args:
        input_path: Path to raw Markdown file.
        output_path: Path where cleaned Markdown will be saved.

    Returns:
        Dictionary containing cleaning metadata.

    Raises:
        MarkdownCleaningError: If cleaning fails.
        FileNotFoundError: If input file not found.
        ValueError: If input file is not Markdown.
    """

    input_file = Path(input_path)
    output_file = Path(output_path)

    # Validate input
    if not input_file.exists():
        raise FileNotFoundError(
            f"Markdown file not found: {input_file}"
        )

    if input_file.suffix.lower() not in [".md", ".markdown"]:
        raise ValueError(
            f"Input file must be Markdown, got: {input_file.suffix}"
        )

    logger.info(
        "Starting lossless Markdown cleaning | file=%s",
        input_file.name,
    )

    try:

        # Read original Markdown
        raw_markdown = input_file.read_text(encoding="utf-8")
        original_characters = len(raw_markdown)

        # Clean
        cleaned_markdown = clean_markdown(raw_markdown)

        # Create output directory
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Save cleaned Markdown
        output_file.write_text(cleaned_markdown, encoding="utf-8")

        logger.info(
            "Cleaned Markdown saved | path=%s",
            output_file.name,
        )

        return {
            "status": "success",
            "input_file": str(input_file.resolve()),
            "output_file": str(output_file.resolve()),
            "original_character_count": original_characters,
            "cleaned_character_count": len(cleaned_markdown),
            "character_difference": len(cleaned_markdown) - original_characters,
        }

    except MarkdownCleaningError:
        raise

    except Exception as error:
        logger.exception(
            "Markdown cleaning failed | file=%s",
            input_file.name,
        )
        raise MarkdownCleaningError(
            f"Failed to clean Markdown file '{input_file.name}': {str(error)}"
        ) from error


    print(
        f"Original characters: "
        f"{result['original_character_count']}"
    )

    print(
        f"Cleaned characters: "
        f"{result['cleaned_character_count']}"
    )

    print(
        f"Difference: "
        f"{result['character_difference']}"
    )

    print(
        f"\nSaved to:\n"
        f"{result['output_file']}"
    )
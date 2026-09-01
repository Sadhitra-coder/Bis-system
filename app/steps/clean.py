"""
Step 2: Lossless Markdown Cleaning

Input:
    Raw Markdown extracted by Docling.

Process:
    Performs ONLY safe formatting cleanup.

IMPORTANT:
    This step must NOT delete, summarize, deduplicate,
    reconstruct, merge, infer, or modify document meaning.

    Every piece of textual information must remain available
    for the AI structuring step.

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

    IMPORTANT:
    This function preserves all document information.

    Args:
        markdown: Raw Markdown content.

    Returns:
        Cleaned Markdown content.
    """

    if not isinstance(markdown, str):
        raise TypeError(
            "markdown must be a string."
        )

    if not markdown:
        return markdown

    original_length = len(markdown)

    # --------------------------------------------------
    # 1. Normalize line endings
    #
    # Windows: \r\n
    # Old Mac: \r
    # Unix:    \n
    #
    # Information is unchanged.
    # --------------------------------------------------

    cleaned = markdown.replace("\r\n", "\n")
    cleaned = cleaned.replace("\r", "\n")

    # --------------------------------------------------
    # 2. Decode HTML entities
    #
    # Example:
    # &amp; -> &
    # &nbsp; -> non-breaking space
    #
    # This preserves the actual textual meaning.
    # --------------------------------------------------

    cleaned = html.unescape(cleaned)

    # --------------------------------------------------
    # 3. Normalize special spaces
    #
    # These characters visually represent spaces but can
    # interfere with parsing and embedding.
    #
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
    # These are invisible formatting artifacts.
    #
    # They do not represent actual document information.
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
    # 5. Remove trailing whitespace ONLY
    #
    # Example:
    #
    # "Hello      \n"
    #
    # becomes:
    #
    # "Hello\n"
    #
    # Internal spaces are NOT touched.
    # Table structure is NOT changed.
    # --------------------------------------------------

    lines = cleaned.split("\n")

    cleaned_lines = [
        line.rstrip()
        for line in lines
    ]

    cleaned = "\n".join(cleaned_lines)

    # --------------------------------------------------
    # 6. Limit excessive consecutive blank lines
    #
    # Important:
    # We preserve paragraph separation.
    #
    # 3+ blank lines -> 2 blank lines
    #
    # No textual content is removed.
    # --------------------------------------------------

    cleaned = re.sub(
        r"\n{4,}",
        "\n\n\n",
        cleaned
    )

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
    input_path: str,
    output_path: str,
) -> Dict[str, Any]:
    """
    Read a Markdown file, clean it safely,
    and save the result.

    Args:
        input_path: Path to raw Markdown file.
        output_path: Path for cleaned Markdown file.

    Returns:
        Dictionary containing cleaning metadata.
    """

    input_file = Path(input_path)
    output_file = Path(output_path)

    # --------------------------------------------------
    # Validate input
    # --------------------------------------------------

    if not input_file.exists():
        raise FileNotFoundError(
            f"Markdown file not found: {input_file}"
        )

    if input_file.suffix.lower() not in [".md", ".markdown"]:
        raise ValueError(
            "Input file must be a Markdown file."
        )

    logger.info(
        "Starting lossless Markdown cleaning | file=%s",
        input_file.name,
    )

    try:

        # --------------------------------------------------
        # Read original Markdown
        # --------------------------------------------------

        raw_markdown = input_file.read_text(
            encoding="utf-8"
        )

        original_characters = len(raw_markdown)

        # --------------------------------------------------
        # Clean
        # --------------------------------------------------

        cleaned_markdown = clean_markdown(
            raw_markdown
        )

        # --------------------------------------------------
        # Create output directory
        # --------------------------------------------------

        output_file.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        # --------------------------------------------------
        # Save cleaned Markdown
        # --------------------------------------------------

        output_file.write_text(
            cleaned_markdown,
            encoding="utf-8"
        )

        logger.info(
            "Cleaned Markdown saved | path=%s",
            output_file,
        )

        return {
            "status": "success",
            "input_file": str(
                input_file.resolve()
            ),
            "output_file": str(
                output_file.resolve()
            ),
            "original_character_count": (
                original_characters
            ),
            "cleaned_character_count": len(
                cleaned_markdown
            ),
            "character_difference": (
                len(cleaned_markdown)
                - original_characters
            ),
        }

    except MarkdownCleaningError:
        raise

    except Exception as error:

        logger.exception(
            "Markdown cleaning failed | file=%s",
            input_file.name,
        )

        raise MarkdownCleaningError(
            f"Failed to clean Markdown file "
            f"'{input_file.name}': {str(error)}"
        ) from error


# --------------------------------------------------
# Local test
# --------------------------------------------------

if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s | %(message)s",
    )

    print("\nStarting lossless Markdown cleaning...\n")

    result = clean_markdown_file(
        input_path=(
            "data/markdown/"
            "Product-Manual-30551-V2.md"
        ),
        output_path=(
            "data/cleaned/"
            "Product-Manual-30551-V2_cleaned.md"
        ),
    )

    print("Cleaning successful!\n")

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
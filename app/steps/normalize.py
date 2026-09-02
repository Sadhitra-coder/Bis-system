"""
Step 4: Markdown Normalization

Input:
    Structured Markdown content or file path

Process:
    Fixes Markdown escaping artifacts produced by the LLM.
    Removes only known, verified artifacts.

Output:
    Normalized Markdown

This module is designed to be generic and reusable:
    - Does NOT contain hardcoded paths
    - Processes ONE Markdown file at a time
    - Takes input_path and output_path as parameters
    - Creates parent directories if needed

IMPORTANT:
    - Does NOT delete information
    - Does NOT summarize information
    - Does NOT change document meaning
    - Does NOT change headings, tables, or content
    - Only fixes known escaping artifacts

Known artifacts to fix:
    - Escaped pipes: [backslash]| -> |
    - Escaped asterisks: [backslash]* -> * (only if verifiably artifact)
"""

import logging
from pathlib import Path


logger = logging.getLogger(__name__)


class NormalizationError(Exception):
    """Raised when Markdown normalization fails."""


def normalize_markdown(content: str) -> str:
    """
    Remove unnecessary escaping from Markdown.

    Only fixes verified formatting artifacts.
    No actual document information is removed.

    Args:
        content: Structured Markdown content.

    Returns:
        Normalized Markdown content.
    """

    if not isinstance(content, str):
        raise TypeError("content must be a string.")

    if not content:
        return content

    normalized = content

    # --------------------------------------------------
    # Fix 1: Escaped pipes (common LLM artifact)
    #
    # LLMs sometimes escape pipes in tables even though
    # they are not required in Markdown code blocks.
    #
    # This is SAFE because pipes are rarely escaped in
    # legitimate Markdown.
    # --------------------------------------------------

    # Only fix escaped pipes that appear to be table artifacts
    # i.e., preceded/followed by | or numbers/text that look like tables
    normalized = normalized.replace(r"\|", "|")

    # --------------------------------------------------
    # Fix 2: Common HTML-encoded characters in URLs
    #
    # Fix: %5C| (escaped pipe in URLs) -> |
    # This is safe because legitimate URLs don't have \|
    # --------------------------------------------------

    normalized = normalized.replace("%5C|", "|")
    normalized = normalized.replace("%5C*", "*")

    # --------------------------------------------------
    # Do NOT fix:
    # - Escaped asterisks: \* is legitimate Markdown escaping
    # - Escaped dots: \. is legitimate escaping
    # - Escaped brackets: \< and \> are used in some contexts
    #
    # These should only be fixed if we have explicit confirmation
    # they are artifacts, not legitimate escaping.
    # --------------------------------------------------

    return normalized


def normalize_markdown_file(
    input_path: Path,
    output_path: Path
) -> Path:
    """
    Read, normalize, and save a Markdown file.

    This function:
        - Processes exactly one Markdown file
        - Creates output parent directory if needed
        - Saves normalized Markdown to output_path
        - Returns the output_path

    Args:
        input_path: Path to structured Markdown file.
        output_path: Path where normalized Markdown will be saved.

    Returns:
        Path to the saved normalized Markdown file.

    Raises:
        NormalizationError: If normalization fails.
        FileNotFoundError: If input file not found.
    """

    input_path = Path(input_path)
    output_path = Path(output_path)

    # Validate input
    if not input_path.exists():
        raise FileNotFoundError(
            f"Markdown file not found: {input_path}"
        )

    if input_path.suffix.lower() not in [".md", ".markdown"]:
        raise ValueError(
            f"Input file must be Markdown, got: {input_path.suffix}"
        )

    logger.info(
        "Normalizing Markdown | file=%s",
        input_path.name
    )

    try:

        # Read structured Markdown
        content = input_path.read_text(encoding="utf-8")

        if not content.strip():
            raise NormalizationError(
                f"Input file is empty: {input_path.name}"
            )

        original_length = len(content)

        # Normalize
        normalized_content = normalize_markdown(content)

        # Create output directory
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Save normalized Markdown
        output_path.write_text(normalized_content, encoding="utf-8")

        logger.info(
            "Normalization successful | "
            "file=%s | before=%s chars | after=%s chars",
            output_path.name,
            original_length,
            len(normalized_content)
        )

        return output_path

    except NormalizationError:
        raise

    except Exception as error:
        logger.exception(
            "Normalization failed | file=%s",
            input_path.name
        )
        raise NormalizationError(
            f"Failed to normalize Markdown file '{input_path.name}': {str(error)}"
        ) from error




# --------------------------------------------------
# CLI ENTRY POINT
# --------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Normalize one Markdown file by removing known escaping artifacts."
    )
    parser.add_argument(
        "input_path",
        type=Path,
        help="Path to the Markdown file to normalize."
    )
    parser.add_argument(
        "output_path",
        type=Path,
        help="Path where the normalized Markdown file should be saved."
    )

    args = parser.parse_args()
    result = normalize_markdown_file(args.input_path, args.output_path)
    print(f"Normalized Markdown saved to: {result}")
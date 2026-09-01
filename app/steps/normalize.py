"""
Step 4: Markdown Normalization

Input:
    Structured Markdown files from data/structured/

Process:
    Fixes Markdown escaping artifacts produced by the LLM.

IMPORTANT:
    - Does NOT delete information.
    - Does NOT summarize information.
    - Does NOT change document meaning.
    - Does NOT change headings, tables, or content.
    - Original files remain untouched.

Output:
    Normalized Markdown files in data/normalized/
"""

import logging
from pathlib import Path


# --------------------------------------------------
# LOGGING
# --------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# --------------------------------------------------
# PATHS
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parents[2]

INPUT_DIR = BASE_DIR / "data" / "structured"

OUTPUT_DIR = BASE_DIR / "data" / "normalized"


# --------------------------------------------------
# NORMALIZATION
# --------------------------------------------------

def normalize_markdown(content: str) -> str:
    """
    Remove unnecessary escaping from Markdown.

    Only formatting artifacts are changed.
    No actual document information is removed.
    """

    replacements = {
        r"\|": "|",
        r"\*": "*",
        r"\<": "<",
        r"\>": ">",
        r"\.": ".",
    }

    normalized = content

    for escaped, actual in replacements.items():
        normalized = normalized.replace(
            escaped,
            actual
        )

    return normalized


# --------------------------------------------------
# PROCESS ONE FILE
# --------------------------------------------------

def process_file(file_path: Path) -> Path:
    """
    Normalize one structured Markdown file.
    """

    logger.info(
        "Processing: %s",
        file_path.name
    )

    # Read original structured Markdown
    content = file_path.read_text(
        encoding="utf-8"
    )

    if not content.strip():
        raise ValueError(
            f"File is empty: {file_path.name}"
        )

    # Normalize formatting artifacts
    normalized_content = normalize_markdown(
        content
    )

    # Create output directory if needed
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    # Output filename
    output_file = (
        OUTPUT_DIR
        / f"{file_path.stem}_normalized.md"
    )

    # Save normalized Markdown
    output_file.write_text(
        normalized_content,
        encoding="utf-8"
    )

    logger.info(
        "Saved: %s",
        output_file.name
    )

    logger.info(
        "Characters | Before: %s | After: %s",
        len(content),
        len(normalized_content)
    )

    return output_file


# --------------------------------------------------
# PROCESS ALL FILES
# --------------------------------------------------

def process_all_files() -> None:
    """
    Normalize all Markdown files
    inside data/structured/.
    """

    if not INPUT_DIR.exists():

        raise FileNotFoundError(
            f"Structured directory not found: "
            f"{INPUT_DIR}"
        )

    markdown_files = list(
        INPUT_DIR.glob("*.md")
    )

    logger.info(
        "Found %s Markdown file(s)",
        len(markdown_files)
    )

    if not markdown_files:

        logger.warning(
            "No Markdown files found."
        )

        return

    logger.info(
        "--------------------------------"
    )

    for file_path in markdown_files:

        try:

            process_file(file_path)

        except Exception as error:

            logger.exception(
                "Failed processing %s | %s",
                file_path.name,
                error
            )


# --------------------------------------------------
# MAIN
# --------------------------------------------------

if __name__ == "__main__":

    print(
        "\nStarting Markdown normalization...\n"
    )

    process_all_files()

    print(
        "\nMarkdown normalization finished.\n"
    )
"""
Step 3: AI Document Structuring

Input:
    Cleaned Markdown files

Process:
    Groq LLM analyzes the Markdown and restructures it
    while preserving ALL information.

Output:
    Structured Markdown

IMPORTANT:
    - Input is the cleaned Markdown, not the original raw Markdown.
    - Do NOT delete information.
    - Do NOT summarize.
    - Do NOT invent information.
    - Preserve clauses, sections, annexes, notes and tables.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq


# --------------------------------------------------
# Logging
# --------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# --------------------------------------------------
# Paths
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_DIR = PROJECT_ROOT / "data" / "cleaned"
OUTPUT_DIR = PROJECT_ROOT / "data" / "structured"


# --------------------------------------------------
# Load environment variables
# --------------------------------------------------

load_dotenv(PROJECT_ROOT / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")


if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY not found. "
        "Add it to your .env file."
    )


# --------------------------------------------------
# Groq Client
# --------------------------------------------------

client = Groq(
    api_key=GROQ_API_KEY
)


# --------------------------------------------------
# Prompt
# --------------------------------------------------

SYSTEM_PROMPT = """
You are an expert document structuring agent.

Your task is to restructure extracted and cleaned Markdown
from technical and regulatory documents.

The input may contain:

- Broken headings
- Repeated table cells
- Duplicated text
- Incorrect table alignment
- Split sentences
- OCR artifacts
- Clause numbers
- Subclauses
- Annexes
- Notes
- Tables

Your job is to improve the STRUCTURE while preserving
ALL meaningful information.

STRICT RULES:

1. DO NOT summarize the document.
2. DO NOT remove meaningful information.
3. DO NOT invent information.
4. DO NOT change numerical values.
5. DO NOT change clause numbers.
6. DO NOT merge unrelated sections.
7. Preserve the original meaning exactly.
8. Preserve annexes and notes.
9. Preserve all table information.

TABLE RULES:

- Reconstruct malformed tables into valid Markdown tables.
- Remove only obvious duplicate cells caused by extraction.
- Do not remove unique information.
- If a table structure is uncertain, preserve the information
  instead of guessing.

HEADING RULES:

Use Markdown hierarchy:

# Document Title
## Major Section
### Subsection
#### Clause

Keep clause numbers exactly as provided.

OUTPUT RULES:

Return ONLY the structured Markdown.

Do not explain what you changed.
Do not add commentary.
Do not wrap the output in triple backticks.
"""


# --------------------------------------------------
# Structure one Markdown document
# --------------------------------------------------

def structure_markdown(
    markdown_content: str,
    document_id: str
) -> str:
    """
    Structure one cleaned Markdown document using Groq.
    """

    logger.info(
        "Structuring document | document_id=%s",
        document_id
    )

    response = client.chat.completions.create(
        model="qwen/qwen3.8-27b",
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": (
                    f"Document ID: {document_id}\n\n"
                    "STRUCTURE THE FOLLOWING DOCUMENT:\n\n"
                    f"{markdown_content}"
                )
            }
        ]
    )

    structured_markdown = (
        response
        .choices[0]
        .message
        .content
    )

    if not structured_markdown:
        raise RuntimeError(
            "Groq returned empty structured content."
        )

    return structured_markdown.strip()


# --------------------------------------------------
# Process one file
# --------------------------------------------------

def process_file(
    input_path: Path
) -> None:
    """
    Read one cleaned Markdown file,
    structure it,
    and save the result.
    """

    document_id = input_path.stem

    logger.info(
        "--------------------------------"
    )

    logger.info(
        "Processing: %s",
        input_path.name
    )

    markdown_content = input_path.read_text(
        encoding="utf-8"
    )

    if not markdown_content.strip():

        logger.warning(
            "Skipping empty file: %s",
            input_path.name
        )

        return


    # ----------------------------------------------
    # Structure using Groq
    # ----------------------------------------------

    structured_markdown = structure_markdown(
        markdown_content=markdown_content,
        document_id=document_id
    )


    # ----------------------------------------------
    # Output path
    # ----------------------------------------------

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    output_path = (
        OUTPUT_DIR /
        f"{document_id}_structured.md"
    )


    # ----------------------------------------------
    # Save
    # ----------------------------------------------

    output_path.write_text(
        structured_markdown,
        encoding="utf-8"
    )


    logger.info(
        "Saved structured file: %s",
        output_path.name
    )

    logger.info(
        "Input characters: %s",
        len(markdown_content)
    )

    logger.info(
        "Output characters: %s",
        len(structured_markdown)
    )


# --------------------------------------------------
# Process all files
# --------------------------------------------------

def process_all_files() -> None:
    """
    Process every cleaned Markdown file
    inside data/cleaned.
    """

    if not INPUT_DIR.exists():

        raise FileNotFoundError(
            f"Input directory not found: "
            f"{INPUT_DIR}"
        )


    markdown_files = list(
        INPUT_DIR.glob("*.md")
    )


    if not markdown_files:

        logger.warning(
            "No Markdown files found in: %s",
            INPUT_DIR
        )

        return


    logger.info(
        "Found %s Markdown file(s)",
        len(markdown_files)
    )


    for file_path in markdown_files:

        try:

            process_file(
                file_path
            )

        except Exception as error:

            logger.exception(
                "Failed processing %s | %s",
                file_path.name,
                error
            )


# --------------------------------------------------
# Main
# --------------------------------------------------

if __name__ == "__main__":

    print(
        "\nStarting AI document structuring with Groq...\n"
    )

    process_all_files()

    print(
        "\nDocument structuring finished.\n"
    )
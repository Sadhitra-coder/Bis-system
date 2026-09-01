import json
import logging
import re
from pathlib import Path
from typing import List, Dict, Any


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parents[2]

INPUT_DIR = BASE_DIR / "data" / "normalized"
OUTPUT_DIR = BASE_DIR / "data" / "chunks"

# Maximum preferred chunk size.
# Tables and important structured blocks are preserved where possible.
MAX_CHUNK_CHARS = 4000

# Very small sections are merged with neighbouring sections.
MIN_CHUNK_CHARS = 250


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
# HELPERS
# ============================================================

def sanitize_document_id(filename: str) -> str:
    """
    Creates a clean document ID from a filename.
    """
    name = Path(filename).stem

    name = re.sub(
        r"_normalized$",
        "",
        name,
        flags=re.IGNORECASE
    )

    return name


def is_heading(line: str) -> bool:
    """
    Returns True if a line is a Markdown heading.
    """
    return bool(
        re.match(r"^\s{0,3}#{1,6}\s+\S", line)
    )


def get_heading_level(line: str) -> int:
    """
    Returns Markdown heading level.
    Example:
        ## Heading -> 2
    """
    match = re.match(
        r"^\s*(#{1,6})\s+",
        line
    )

    if match:
        return len(match.group(1))

    return 0


def get_heading_text(line: str) -> str:
    """
    Removes Markdown heading markers.
    """
    return re.sub(
        r"^\s*#{1,6}\s+",
        "",
        line
    ).strip()


def is_table_line(line: str) -> bool:
    """
    Detects Markdown table rows.
    """
    stripped = line.strip()

    return (
        stripped.startswith("|")
        and stripped.endswith("|")
    )


def is_blank(line: str) -> bool:
    """
    Checks whether a line is blank.
    """
    return not line.strip()


# ============================================================
# MARKDOWN PARSING
# ============================================================

def parse_markdown_sections(
    markdown_content: str
) -> List[Dict[str, Any]]:
    """
    Parses Markdown into logical sections.

    Each section contains:
        - heading
        - heading_level
        - heading_context
        - content

    Information is never removed.
    """

    lines = markdown_content.splitlines()

    sections = []

    heading_stack = []

    current_section = None


    def save_current_section():
        nonlocal current_section

        if current_section is None:
            return

        content = "\n".join(
            current_section["lines"]
        ).strip()

        if content:
            sections.append(
                {
                    "heading": current_section["heading"],
                    "heading_level": current_section[
                        "heading_level"
                    ],
                    "heading_context": current_section[
                        "heading_context"
                    ],
                    "content": content
                }
            )

        current_section = None


    for line in lines:

        if is_heading(line):

            save_current_section()

            level = get_heading_level(line)

            heading_text = get_heading_text(line)

            # Remove headings at the same or deeper level.
            while (
                heading_stack
                and heading_stack[-1]["level"] >= level
            ):
                heading_stack.pop()

            heading_stack.append(
                {
                    "level": level,
                    "text": heading_text
                }
            )

            heading_context = [
                item["text"]
                for item in heading_stack
            ]

            current_section = {
                "heading": heading_text,
                "heading_level": level,
                "heading_context": heading_context,
                "lines": [line]
            }

        else:

            # Content before first heading.
            if current_section is None:

                if line.strip():

                    current_section = {
                        "heading": "Document Content",
                        "heading_level": 0,
                        "heading_context": [],
                        "lines": []
                    }

                else:
                    continue

            current_section["lines"].append(
                line
            )


    save_current_section()

    return sections


# ============================================================
# SECTION MERGING
# ============================================================

def is_heading_only_section(
    section: Dict[str, Any]
) -> bool:
    """
    Checks whether a section contains only its heading.
    """

    content_lines = [
        line.strip()
        for line in section["content"].splitlines()
        if line.strip()
    ]

    return len(content_lines) == 1 and is_heading(
        content_lines[0]
    )


def merge_sections(
    sections: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Merges logically connected small sections.

    Rules:
    1. Heading-only sections are merged with the next section.
    2. Very small sections can be merged with the next related section.
    3. Large sections remain independent.
    4. No content is deleted.
    """

    merged = []

    index = 0

    while index < len(sections):

        current = sections[index]

        current_content = current["content"]

        current_size = len(current_content)


        # ----------------------------------------------------
        # RULE 1:
        # Heading-only section + next section
        # ----------------------------------------------------

        if (
            is_heading_only_section(current)
            and index + 1 < len(sections)
        ):

            next_section = sections[index + 1]

            combined_content = (
                current["content"].rstrip()
                + "\n\n"
                + next_section["content"].lstrip()
            )

            merged.append(
                {
                    "heading": next_section["heading"],
                    "heading_level": next_section[
                        "heading_level"
                    ],
                    "heading_context": next_section[
                        "heading_context"
                    ],
                    "content": combined_content
                }
            )

            index += 2

            continue


        # ----------------------------------------------------
        # RULE 2:
        # Small section + related next section
        # ----------------------------------------------------

        if (
            current_size < MIN_CHUNK_CHARS
            and index + 1 < len(sections)
        ):

            next_section = sections[index + 1]

            combined_size = (
                current_size
                + len(next_section["content"])
            )

            # Merge only if resulting chunk remains reasonable.
            if combined_size <= MAX_CHUNK_CHARS:

                combined_content = (
                    current["content"].rstrip()
                    + "\n\n"
                    + next_section["content"].lstrip()
                )

                merged.append(
                    {
                        "heading": current["heading"],
                        "heading_level": current[
                            "heading_level"
                        ],
                        "heading_context": current[
                            "heading_context"
                        ],
                        "content": combined_content
                    }
                )

                index += 2

                continue


        # ----------------------------------------------------
        # DEFAULT:
        # Keep section unchanged
        # ----------------------------------------------------

        merged.append(current)

        index += 1


    return merged


# ============================================================
# LARGE SECTION SPLITTING
# ============================================================

def split_large_section(
    section: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Splits genuinely large sections.

    Priority:
        1. Preserve tables.
        2. Split on paragraph boundaries.
        3. Never remove content.
    """

    content = section["content"]

    if len(content) <= MAX_CHUNK_CHARS:
        return [section]


    lines = content.splitlines()

    chunks = []

    current_lines = []

    current_length = 0


    def save_chunk():

        nonlocal current_lines
        nonlocal current_length

        chunk_text = "\n".join(
            current_lines
        ).strip()

        if chunk_text:

            chunks.append(
                {
                    "heading": section["heading"],
                    "heading_level": section[
                        "heading_level"
                    ],
                    "heading_context": section[
                        "heading_context"
                    ],
                    "content": chunk_text
                }
            )

        current_lines = []

        current_length = 0


    index = 0

    while index < len(lines):

        line = lines[index]

        line_length = len(line) + 1


        # ----------------------------------------------------
        # Detect complete Markdown table block
        # ----------------------------------------------------

        if is_table_line(line):

            table_lines = []

            while (
                index < len(lines)
                and is_table_line(lines[index])
            ):

                table_lines.append(
                    lines[index]
                )

                index += 1


            table_text = "\n".join(
                table_lines
            )

            table_length = len(table_text)


            # If table fits with current content.
            if (
                current_length
                + table_length
                <= MAX_CHUNK_CHARS
            ):

                current_lines.extend(
                    table_lines
                )

                current_length += table_length

            else:

                # Save previous text first.
                save_chunk()


                # Table itself is larger than limit.
                # Preserve it as much as possible.
                if table_length <= MAX_CHUNK_CHARS:

                    current_lines.extend(
                        table_lines
                    )

                    current_length = table_length

                else:

                    # Split very large table row-by-row,
                    # while repeating header.
                    split_table(
                        table_lines,
                        section,
                        chunks
                    )


            continue


        # ----------------------------------------------------
        # Normal line processing
        # ----------------------------------------------------

        if (
            current_length
            + line_length
            > MAX_CHUNK_CHARS
            and current_lines
        ):

            save_chunk()


        current_lines.append(line)

        current_length += line_length

        index += 1


    save_chunk()

    return chunks


# ============================================================
# LARGE TABLE SPLITTING
# ============================================================

def split_table(
    table_lines: List[str],
    section: Dict[str, Any],
    chunks: List[Dict[str, Any]]
):
    """
    Splits a very large Markdown table.

    The header and separator rows are repeated
    so every chunk remains understandable.
    """

    if len(table_lines) < 3:

        chunks.append(
            {
                "heading": section["heading"],
                "heading_level": section[
                    "heading_level"
                ],
                "heading_context": section[
                    "heading_context"
                ],
                "content": "\n".join(
                    table_lines
                )
            }
        )

        return


    header = table_lines[0]

    separator = table_lines[1]

    rows = table_lines[2:]


    current_lines = [
        header,
        separator
    ]

    current_length = len(
        "\n".join(current_lines)
    )


    for row in rows:

        row_length = len(row) + 1

        if (
            current_length
            + row_length
            > MAX_CHUNK_CHARS
            and len(current_lines) > 2
        ):

            chunks.append(
                {
                    "heading": section["heading"],
                    "heading_level": section[
                        "heading_level"
                    ],
                    "heading_context": section[
                        "heading_context"
                    ],
                    "content": "\n".join(
                        current_lines
                    )
                }
            )

            current_lines = [
                header,
                separator
            ]

            current_length = len(
                "\n".join(current_lines)
            )


        current_lines.append(row)

        current_length += row_length


    if len(current_lines) > 2:

        chunks.append(
            {
                "heading": section["heading"],
                "heading_level": section[
                    "heading_level"
                ],
                "heading_context": section[
                    "heading_context"
                ],
                "content": "\n".join(
                    current_lines
                )
            }
        )


# ============================================================
# CHUNK DOCUMENT
# ============================================================

def create_chunks(
    markdown_content: str,
    document_id: str,
    source_file: str
) -> List[Dict[str, Any]]:
    """
    Complete chunking pipeline.

    Markdown
        ↓
    Parse sections
        ↓
    Merge small / heading-only sections
        ↓
    Split large sections
        ↓
    Create final chunks
    """

    logger.info(
        f"Parsing document | document_id={document_id}"
    )

    sections = parse_markdown_sections(
        markdown_content
    )


    logger.info(
        f"Sections found: {len(sections)}"
    )


    sections = merge_sections(
        sections
    )


    logger.info(
        f"Sections after smart merging: "
        f"{len(sections)}"
    )


    final_sections = []

    for section in sections:

        split_sections = split_large_section(
            section
        )

        final_sections.extend(
            split_sections
        )


    chunks = []

    for index, section in enumerate(
        final_sections,
        start=1
    ):

        chunk_id = (
            f"{document_id}"
            f"_chunk_{index:04d}"
        )

        content = section[
            "content"
        ].strip()


        chunks.append(
            {
                "chunk_id": chunk_id,
                "document_id": document_id,
                "source_file": source_file,
                "section": section["heading"],
                "heading_context": section[
                    "heading_context"
                ],
                "content": content,
                "character_count": len(
                    content
                )
            }
        )


    return chunks


# ============================================================
# PROCESS SINGLE FILE
# ============================================================

def process_file(
    file_path: Path
):
    """
    Processes one normalized Markdown file.
    """

    logger.info(
        f"Processing: {file_path.name}"
    )


    markdown_content = file_path.read_text(
        encoding="utf-8"
    )


    document_id = sanitize_document_id(
        file_path.name
    )


    chunks = create_chunks(
        markdown_content=markdown_content,
        document_id=document_id,
        source_file=file_path.name
    )


    output_data = {
        "document_id": document_id,
        "source_file": file_path.name,
        "total_chunks": len(chunks),
        "chunks": chunks
    }


    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )


    output_path = (
        OUTPUT_DIR
        / f"{document_id}_chunks.json"
    )


    with open(
        output_path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            output_data,
            file,
            ensure_ascii=False,
            indent=2
        )


    logger.info(
        f"Created {len(chunks)} chunks"
    )

    logger.info(
        f"Saved: {output_path}"
    )


# ============================================================
# PROCESS ALL FILES
# ============================================================

def process_all_files():

    logger.info(
        "Starting smart document chunking..."
    )


    if not INPUT_DIR.exists():

        logger.error(
            f"Input directory not found: "
            f"{INPUT_DIR}"
        )

        return


    markdown_files = list(
        INPUT_DIR.glob("*.md")
    )


    logger.info(
        f"Found {len(markdown_files)} "
        f"Markdown file(s)"
    )


    if not markdown_files:

        logger.warning(
            "No Markdown files found."
        )

        return


    for file_path in markdown_files:

        logger.info(
            "-" * 50
        )

        try:

            process_file(
                file_path
            )

        except Exception:

            logger.exception(
                f"Failed processing "
                f"{file_path.name}"
            )


    logger.info(
        "Document chunking finished."
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    process_all_files()
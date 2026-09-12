"""
structure.py

LOSSLESS GENERIC DOCUMENT STRUCTURING
=====================================

PHILOSOPHY
----------

Python determines:
    - document boundaries
    - paragraphs
    - markdown tables
    - candidate headings
    - block IDs
    - preservation validation

LLM determines:
    - whether a heading candidate is actually a heading
    - semantic heading hierarchy
    - semantic section organization

IMPORTANT:
----------
The LLM NEVER rewrites document content.

The LLM NEVER returns the document text.

The LLM ONLY returns decisions about Python-created blocks.

Therefore:

    Python boundaries
            +
    LLM semantic decisions
            +
    Lossless validation

= robust generic document structuring


USAGE
-----

    python -m app.steps.structure input.md output.md

    (JSON sidecar is written next to the Markdown output.)


PIPELINE CONTRACT
-----------------

The canonical inter-stage format is MARKDOWN.

    stage 3 structure  -> *_structured.md   (+ *_structured.json sidecar)
    stage 4 normalize  -> *_normalized.md
    stage 5 chunk      -> *_chunks.json

structure_markdown_file() renders the semantic block list back
to Markdown so that normalize and chunk keep working on
Markdown. The full structured record (blocks, document tree,
validation, raw text) is written alongside as a JSON sidecar so
nothing is lost.


ENVIRONMENT
-----------

All configuration comes from app.config.settings, which reads
the project .env. See .env.example.
"""

import json
import logging
import re
import sys

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import BASE_DIR, settings
from app.llm_client import GroqClient, LLMUnavailableError


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURATION
#
# All values come from app.config.settings.
# This module performs no environment lookups of its own.
# ============================================================

GROQ_MODEL = settings.GROQ_STRUCTURE_MODEL

DEFAULT_BATCH_SIZE = settings.STRUCTURE_BATCH_SIZE

MAX_LLM_OUTPUT_TOKENS = settings.STRUCTURE_MAX_OUTPUT_TOKENS


# ============================================================
# ERRORS
# ============================================================

class StructuringError(Exception):
    """Raised when the structuring stage cannot complete."""


# ============================================================
# BASIC NORMALIZATION
# ============================================================

def normalize_whitespace(text: str) -> str:
    """
    Normalize whitespace conservatively.

    Does NOT remove information.
    """

    if text is None:
        return ""

    text = text.replace("\u00a0", " ")
    text = text.replace("\u200b", "")

    text = re.sub(
        r"[ \t]+",
        " ",
        text
    )

    return text.strip()


def normalize_cell(cell: str) -> str:
    """
    Conservative table cell normalization.
    """

    if cell is None:
        return ""

    cell = cell.replace("\u00a0", " ")
    cell = cell.replace("\u200b", "")

    cell = re.sub(
        r"[ \t]+",
        " ",
        cell
    )

    return cell.strip()


def normalize_for_comparison(text: str) -> str:
    """
    Used ONLY for duplicate comparison.

    Never used to replace output text.
    """

    text = normalize_cell(text).lower()

    text = re.sub(
        r"[^\w\s]",
        "",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# DUPLICATE DETECTION
# ============================================================

def are_duplicates(
    a: str,
    b: str
) -> bool:
    """
    Conservative duplicate detection.
    """

    a_normalized = normalize_for_comparison(a)

    b_normalized = normalize_for_comparison(b)

    if not a_normalized:
        return False

    if not b_normalized:
        return False

    return a_normalized == b_normalized


def deduplicate_adjacent_cells(
    cells: List[str]
) -> List[str]:
    """
    Remove ONLY adjacent duplicate cells.

    Example:

        ["Type", "Type", "Bearing"]

    becomes:

        ["Type", "Bearing"]

    Non-adjacent duplicates are preserved.
    """

    if not cells:
        return []

    result = []

    for cell in cells:

        cell = normalize_cell(cell)

        if not result:
            result.append(cell)
            continue

        previous = result[-1]

        if (
            cell
            and previous
            and are_duplicates(cell, previous)
        ):
            continue

        result.append(cell)

    return result


def remove_empty_edges(
    cells: List[str]
) -> List[str]:
    """
    Remove empty cells ONLY from beginning and end.

    Internal empty cells are preserved.
    """

    cells = list(cells)

    while (
        cells
        and not normalize_cell(cells[0])
    ):
        cells.pop(0)

    while (
        cells
        and not normalize_cell(cells[-1])
    ):
        cells.pop()

    return cells


# ============================================================
# TABLE DETECTION
# ============================================================

def is_table_separator(
    line: str
) -> bool:
    """
    Detect markdown table separator.

    Example:

        |------|------|
        | :--- | ---: |
    """

    stripped = line.strip()

    if "|" not in stripped:
        return False

    candidate = stripped

    candidate = candidate.replace("|", "")
    candidate = candidate.replace(":", "")
    candidate = candidate.replace("-", "")
    candidate = candidate.replace(" ", "")

    return candidate == ""


def is_markdown_table_line(
    line: str
) -> bool:
    """
    Conservative markdown table detection.
    """

    stripped = line.strip()

    if not stripped:
        return False

    if not stripped.startswith("|"):
        return False

    if "|" not in stripped:
        return False

    return True


def split_table_row(
    line: str
) -> List[str]:
    """
    Split a markdown table row.
    """

    line = line.strip()

    if line.startswith("|"):
        line = line[1:]

    if line.endswith("|"):
        line = line[:-1]

    cells = line.split("|")

    cells = [
        normalize_cell(cell)
        for cell in cells
    ]

    cells = deduplicate_adjacent_cells(cells)

    cells = remove_empty_edges(cells)

    return cells


# ============================================================
# TABLE CLEANING
# ============================================================

def clean_table_rows(
    rows: List[List[str]]
) -> List[List[str]]:
    """
    Clean rows WITHOUT forcing rectangular structure.

    This is important because PDFs may contain:

        - merged cells
        - broken columns
        - row spans
        - extraction artifacts

    Information is never discarded simply because a row
    has a different number of columns.
    """

    cleaned_rows = []

    for row in rows:

        if not row:
            continue

        cleaned = [
            normalize_cell(cell)
            for cell in row
        ]

        cleaned = deduplicate_adjacent_cells(cleaned)

        cleaned = remove_empty_edges(cleaned)

        if any(cleaned):
            cleaned_rows.append(cleaned)

    return cleaned_rows


def row_signature(
    row: List[str]
) -> str:
    """
    Used only for immediately repeated rows.
    """

    return " || ".join(
        normalize_for_comparison(cell)
        for cell in row
    )


def deduplicate_adjacent_rows(
    rows: List[List[str]]
) -> List[List[str]]:
    """
    Remove ONLY immediately repeated identical rows.
    """

    if not rows:
        return []

    result = []

    previous_signature = None

    for row in rows:

        signature = row_signature(row)

        if (
            signature
            and signature == previous_signature
        ):
            continue

        result.append(row)

        previous_signature = signature

    return result


# ============================================================
# HEADING CANDIDATE DETECTION
# ============================================================

def strip_markdown_heading(
    line: str
) -> str:
    """
    Convert:

        ## TITLE

    into:

        TITLE
    """

    return re.sub(
        r"^\s{0,3}#{1,6}\s+",
        "",
        line
    ).strip()


def get_markdown_heading_level(
    line: str
) -> Optional[int]:
    """
    Return markdown heading level.

    Example:

        ### Title

    returns:

        3
    """

    match = re.match(
        r"^\s{0,3}(#{1,6})\s+",
        line
    )

    if not match:
        return None

    return len(match.group(1))


def looks_like_heading_candidate(
    text: str
) -> bool:
    """
    Conservative Python heading candidate detection.

    IMPORTANT:

    Python is NOT deciding that something is definitely
    a heading.

    Python is only creating candidates.

    The LLM will make the semantic decision.
    """

    text = normalize_whitespace(text)

    if not text:
        return False

    if len(text) > 180:
        return False

    # Markdown-like section markers.

    if re.match(
        r"^(ANNEX|APPENDIX|SECTION|CHAPTER|PART)\b",
        text,
        flags=re.IGNORECASE
    ):
        return True

    # Numbered structures.

    if re.match(
        r"^\d+(?:\.\d+)*\.?\s+[A-Za-z]",
        text
    ):
        return True

    # Lettered structures.

    if re.match(
        r"^[A-Z]\.\s+[A-Za-z]",
        text
    ):
        return True

    # Roman numerals.

    if re.match(
        r"^[IVXLCDM]+\.\s+[A-Za-z]",
        text,
        flags=re.IGNORECASE
    ):
        return True

    # Short uppercase text.

    letters = re.sub(
        r"[^A-Za-z]",
        "",
        text
    )

    if (
        letters
        and len(letters) >= 4
        and len(text) < 120
        and letters.isupper()
    ):
        return True

    return False


def infer_python_heading_level(
    text: str
) -> int:
    """
    Python provisional level.

    The LLM may override this.

    Used as fallback when LLM is unavailable.
    """

    text = normalize_whitespace(text)

    match = re.match(
        r"^(\d+(?:\.\d+)*)\.?\s+",
        text
    )

    if match:

        numbering = match.group(1)

        return numbering.count(".") + 1

    if re.match(
        r"^(ANNEX|APPENDIX)\b",
        text,
        flags=re.IGNORECASE
    ):
        return 1

    return 1


# ============================================================
# BLOCK CREATION
# ============================================================

def create_paragraph_block(
    block_id: str,
    lines: List[str],
    heading_candidate: bool = False
) -> Dict[str, Any]:
    """
    Create paragraph block.

    source_lines preserve original extracted lines.
    """

    original_lines = [
        line.rstrip()
        for line in lines
        if line.strip()
    ]

    text = " ".join(
        normalize_whitespace(line)
        for line in original_lines
    )

    return {
        "id": block_id,
        "type": "paragraph",
        "text": text,
        "source_lines": original_lines,
        "heading_candidate": heading_candidate,
        "python_heading_level": (
            infer_python_heading_level(text)
            if heading_candidate
            else None
        )
    }


def create_heading_block(
    block_id: str,
    text: str,
    level: int
) -> Dict[str, Any]:
    """
    Create definite markdown heading block.
    """

    return {
        "id": block_id,
        "type": "heading",
        "text": normalize_whitespace(text),
        "source_lines": [
            text.rstrip()
        ],
        "heading_candidate": True,
        "python_heading_level": level
    }


def create_table_block(
    block_id: str,
    rows: List[List[str]],
    raw_lines: List[str]
) -> Dict[str, Any]:
    """
    Create table block.

    Both structured rows AND raw extracted lines are kept.

    Raw lines guarantee preservation.
    """

    cleaned_rows = clean_table_rows(rows)

    cleaned_rows = deduplicate_adjacent_rows(
        cleaned_rows
    )

    max_columns = max(
        (
            len(row)
            for row in cleaned_rows
        ),
        default=0
    )

    return {
        "id": block_id,
        "type": "table",
        "rows": cleaned_rows,
        "raw_lines": [
            line.rstrip()
            for line in raw_lines
        ],
        "row_count": len(cleaned_rows),
        "max_columns": max_columns
    }


# ============================================================
# PYTHON BOUNDARY PARSER
# ============================================================

def parse_blocks(
    text: str
) -> List[Dict[str, Any]]:
    """
    Python determines document boundaries.

    Output blocks are:

        - heading
        - paragraph
        - table

    Nothing is semantically rewritten here.
    """

    lines = text.splitlines()

    blocks: List[Dict[str, Any]] = []

    paragraph_buffer: List[str] = []

    table_rows: List[List[str]] = []

    table_raw_lines: List[str] = []

    in_table = False

    block_counter = 0


    def next_id() -> str:

        nonlocal block_counter

        block_counter += 1

        return f"block_{block_counter:05d}"


    def flush_paragraph():

        nonlocal paragraph_buffer

        if not paragraph_buffer:
            return

        candidate_text = " ".join(
            normalize_whitespace(line)
            for line in paragraph_buffer
            if line.strip()
        )

        if not candidate_text:

            paragraph_buffer = []

            return

        heading_candidate = (
            len(paragraph_buffer) == 1
            and looks_like_heading_candidate(
                candidate_text
            )
        )

        block = create_paragraph_block(
            block_id=next_id(),
            lines=paragraph_buffer,
            heading_candidate=heading_candidate
        )

        blocks.append(block)

        paragraph_buffer = []


    def flush_table():

        nonlocal table_rows
        nonlocal table_raw_lines
        nonlocal in_table

        if not table_rows and not table_raw_lines:

            in_table = False

            return

        block = create_table_block(
            block_id=next_id(),
            rows=table_rows,
            raw_lines=table_raw_lines
        )

        blocks.append(block)

        table_rows = []

        table_raw_lines = []

        in_table = False


    for raw_line in lines:

        line = raw_line.rstrip("\n")

        stripped = line.strip()


        # ----------------------------------------------------
        # EMPTY LINE
        # ----------------------------------------------------

        if not stripped:

            if in_table:
                flush_table()

            flush_paragraph()

            continue


        # ----------------------------------------------------
        # MARKDOWN TABLE
        # ----------------------------------------------------

        if is_markdown_table_line(stripped):

            flush_paragraph()

            in_table = True

            table_raw_lines.append(
                line
            )

            if not is_table_separator(stripped):

                row = split_table_row(
                    stripped
                )

                if row:
                    table_rows.append(
                        row
                    )

            continue


        # ----------------------------------------------------
        # NON-TABLE AFTER TABLE
        # ----------------------------------------------------

        if in_table:
            flush_table()


        # ----------------------------------------------------
        # MARKDOWN HEADING
        # ----------------------------------------------------

        markdown_level = (
            get_markdown_heading_level(
                stripped
            )
        )

        if markdown_level is not None:

            flush_paragraph()

            heading_text = (
                strip_markdown_heading(
                    stripped
                )
            )

            blocks.append(
                create_heading_block(
                    block_id=next_id(),
                    text=heading_text,
                    level=markdown_level
                )
            )

            continue


        # ----------------------------------------------------
        # NORMAL CONTENT
        # ----------------------------------------------------

        # If a single line strongly looks like a heading,
        # isolate it as its own paragraph candidate.

        if (
            looks_like_heading_candidate(stripped)
            and not paragraph_buffer
        ):

            paragraph_buffer.append(
                line
            )

            flush_paragraph()

            continue


        paragraph_buffer.append(
            line
        )


    # Final flush.

    if in_table:
        flush_table()

    flush_paragraph()

    return blocks


# ============================================================
# LLM INITIALIZATION
# ============================================================

def initialize_llm():
    """
    Initialize the shared Groq client.

    Returns
    -------
    (client, error_message)

        client is None when the LLM is intentionally disabled
        (settings.LLM_ENABLED is False) or cannot be used
        (missing API key). error_message explains which.

    A None client is a CONFIGURATION state, not a failure of an
    API call. Deliberately disabling the LLM is allowed to fall
    back to the deterministic classifier. An API call that fails
    at runtime is handled separately in semantic_structure().
    """

    if not settings.LLM_ENABLED:

        return (
            None,
            "LLM disabled by configuration (LLM_ENABLED=false)."
        )

    try:

        client = GroqClient()

        return (
            client,
            None
        )

    except LLMUnavailableError as error:

        return (
            None,
            str(error)
        )


# ============================================================
# LLM PROMPT
# ============================================================

def build_llm_prompt(
    batch: List[Dict[str, Any]]
) -> str:
    """
    Build semantic structuring prompt.

    CRITICAL DESIGN:

    The LLM receives block IDs and text.

    It NEVER rewrites content.

    It ONLY classifies the blocks.
    """

    llm_blocks = []

    for block in batch:

        item = {
            "id": block["id"],
            "python_type": block["type"],
            "heading_candidate": block.get(
                "heading_candidate",
                False
            ),
            "python_heading_level": block.get(
                "python_heading_level"
            )
        }


        if block["type"] in (
            "heading",
            "paragraph"
        ):

            item["text"] = block.get(
                "text",
                ""
            )


        elif block["type"] == "table":

            # We do not ask LLM to reconstruct tables.

            # Give it only structural context.

            item["row_count"] = block.get(
                "row_count",
                0
            )

            item["max_columns"] = block.get(
                "max_columns",
                0
            )

            item["preview"] = (
                block.get(
                    "raw_lines",
                    []
                )[:3]
            )


        llm_blocks.append(
            item
        )


    blocks_json = json.dumps(
        llm_blocks,
        ensure_ascii=False,
        indent=2
    )


    prompt = f"""
You are a DOCUMENT STRUCTURE CLASSIFIER.

Your job is NOT to rewrite a document.

Your job is NOT to summarize a document.

Your job is NOT to extract information into a new schema.

Your ONLY task is to make semantic decisions about already-created
document blocks.

The Python program has already determined all boundaries.

Every block has an immutable ID.

============================================================
CORE PHILOSOPHY
============================================================

Python determines boundaries.

You determine semantic structure.

You MUST NOT change, remove, merge, split, paraphrase, summarize,
or invent document content.

============================================================
TASK
============================================================

For every block, decide whether it functions as:

1. heading
2. content

TABLE blocks must remain tables.

Do not classify tables as headings.

For heading blocks, determine a logical hierarchy level.

Level meanings:

    1 = major section
    2 = subsection
    3 = sub-subsection
    4 = deeper subsection

Do not create unnecessary deep levels.

Use the numbering and surrounding document context.

============================================================
VERY IMPORTANT
============================================================

You are making decisions about the existing blocks.

You are NOT generating a new document.

You MUST return every input ID exactly once.

Do not omit IDs.

Do not create IDs.

Do not rename IDs.

============================================================
NEGATIVE INSTRUCTIONS
============================================================

DO NOT:

- rewrite any block text
- summarize text
- remove information
- combine blocks
- split blocks
- repair OCR content
- correct spelling
- normalize technical values
- modify numbers
- modify units
- modify standards
- reconstruct tables
- invent headings
- invent sections
- omit blocks
- reorder blocks
- return explanations
- return markdown
- return anything outside JSON

If uncertain whether a block is a heading:

    KEEP IT AS CONTENT.

False heading classification is worse than keeping content
as a paragraph.

============================================================
OUTPUT FORMAT
============================================================

Return EXACTLY this JSON structure:

{{
  "items": [
    {{
      "id": "block_00001",
      "semantic_type": "heading",
      "level": 1,
      "confidence": 0.95
    }},
    {{
      "id": "block_00002",
      "semantic_type": "content",
      "level": null,
      "confidence": 0.80
    }}
  ]
}}

RULES:

- Every input ID exactly once.
- semantic_type must be only "heading" or "content".
- Table blocks must always return "content".
- level must be an integer 1 to 4 for headings.
- level must be null for content.
- confidence must be between 0 and 1.

============================================================
BLOCKS
============================================================

{blocks_json}
"""

    return prompt


# ============================================================
# LLM RESPONSE PARSING
# ============================================================

def parse_llm_response(
    response_text: str
) -> Dict[str, Any]:
    """
    Parse JSON returned by LLM.

    Includes fallback extraction in case the model wraps
    JSON with extra whitespace.
    """

    response_text = response_text.strip()

    try:

        return json.loads(
            response_text
        )

    except json.JSONDecodeError:

        match = re.search(
            r"\{.*\}",
            response_text,
            flags=re.DOTALL
        )

        if not match:
            raise ValueError(
                "LLM did not return valid JSON."
            )

        return json.loads(
            match.group(0)
        )


# ============================================================
# LLM BATCH VALIDATION
# ============================================================

def validate_llm_decisions(
    batch: List[Dict[str, Any]],
    result: Dict[str, Any]
) -> Tuple[bool, str]:
    """
    Strict validation.

    We reject the entire LLM batch if:

        - IDs are missing
        - extra IDs exist
        - IDs are duplicated
        - invalid semantic type exists
        - invalid level exists
    """

    if not isinstance(result, dict):

        return (
            False,
            "LLM result is not an object."
        )


    items = result.get("items")


    if not isinstance(items, list):

        return (
            False,
            "LLM result has no valid items list."
        )


    input_ids = [
        block["id"]
        for block in batch
    ]

    output_ids = [
        item.get("id")
        for item in items
        if isinstance(item, dict)
    ]


    if len(output_ids) != len(input_ids):

        return (
            False,
            "LLM returned incorrect number of items."
        )


    if len(set(output_ids)) != len(output_ids):

        return (
            False,
            "LLM returned duplicate IDs."
        )


    if set(output_ids) != set(input_ids):

        missing = (
            set(input_ids)
            - set(output_ids)
        )

        extra = (
            set(output_ids)
            - set(input_ids)
        )

        return (
            False,
            (
                f"ID mismatch. "
                f"Missing={list(missing)} "
                f"Extra={list(extra)}"
            )
        )


    for item in items:

        semantic_type = item.get(
            "semantic_type"
        )


        if semantic_type not in (
            "heading",
            "content"
        ):

            return (
                False,
                f"Invalid semantic_type: {semantic_type}"
            )


        level = item.get(
            "level"
        )


        if semantic_type == "heading":

            if (
                not isinstance(level, int)
                or level < 1
                or level > 4
            ):

                return (
                    False,
                    f"Invalid heading level: {level}"
                )


    return (
        True,
        "OK"
    )


# ============================================================
# PYTHON FALLBACK
# ============================================================

def python_fallback_decisions(
    batch: List[Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """
    Safe fallback when LLM fails.

    Python uses conservative candidate detection.

    Tables are always content.
    """

    decisions = {}


    for block in batch:

        block_id = block["id"]


        if block["type"] == "heading":

            decisions[block_id] = {
                "semantic_type": "heading",
                "level": block.get(
                    "python_heading_level",
                    1
                ),
                "confidence": 1.0,
                "source": "python"
            }

            continue


        if (
            block["type"] == "paragraph"
            and block.get(
                "heading_candidate",
                False
            )
        ):

            decisions[block_id] = {
                "semantic_type": "heading",
                "level": block.get(
                    "python_heading_level",
                    1
                ),
                "confidence": 0.60,
                "source": "python"
            }

            continue


        decisions[block_id] = {
            "semantic_type": "content",
            "level": None,
            "confidence": 1.0,
            "source": "python"
        }


    return decisions


# ============================================================
# SEMANTIC STRUCTURING
# ============================================================

def semantic_structure(
    blocks: List[Dict[str, Any]]
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, Any]
]:
    """
    Perform semantic structure classification.

    Returns:

        decisions_by_id
        llm_metadata
    """

    client, initialization_error = (
        initialize_llm()
    )


    # --------------------------------------------------------
    # LLM UNAVAILABLE
    #
    # Two distinct cases:
    #
    #   1. LLM_ENABLED=false
    #      Deliberate configuration. Use the deterministic
    #      classifier and record that the LLM was not used.
    #
    #   2. LLM_ENABLED=true but unusable (no API key).
    #      This is a misconfiguration, not a choice. Refuse to
    #      pretend the document was structured by the LLM.
    # --------------------------------------------------------

    if client is None:

        if settings.LLM_ENABLED:

            raise StructuringError(
                "LLM structuring is enabled but the LLM is "
                f"unavailable: {initialization_error} "
                "Set LLM_ENABLED=false to structure documents "
                "with the deterministic classifier instead."
            )

        logger.warning(
            "LLM structuring disabled: %s "
            "Using the deterministic Python classifier.",
            initialization_error
        )


        decisions = (
            python_fallback_decisions(
                blocks
            )
        )


        return (
            decisions,
            {
                "enabled": False,
                "used": False,
                "model": GROQ_MODEL,
                "reason": initialization_error,
                "llm_batches_used": 0,
                "fallback_batches": 0
            }
        )


    # --------------------------------------------------------
    # BATCH PROCESSING
    # --------------------------------------------------------

    decisions: Dict[
        str,
        Dict[str, Any]
    ] = {}


    llm_batches_used = 0

    fallback_batches = 0


    for start in range(
        0,
        len(blocks),
        DEFAULT_BATCH_SIZE
    ):

        batch = blocks[
            start:
            start + DEFAULT_BATCH_SIZE
        ]


        batch_number = (
            start // DEFAULT_BATCH_SIZE
        ) + 1


        logger.info(
            "LLM structuring batch %d "
            "(%d blocks).",
            batch_number,
            len(batch)
        )


        prompt = build_llm_prompt(
            batch
        )


        try:

            # GroqClient handles retry / backoff for
            # transient failures. Anything raised here is
            # already unrecoverable.
            response_text = (
                client.chat_completion(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a strict JSON-only "
                                "document structure classifier."
                            )
                        },
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    model=GROQ_MODEL,
                    temperature=0,
                    max_completion_tokens=(
                        MAX_LLM_OUTPUT_TOKENS
                    ),
                    response_format={
                        "type": "json_object"
                    },
                    description=(
                        f"structuring batch {batch_number}"
                    )
                )
            )


            parsed = parse_llm_response(
                response_text
            )


            valid, reason = (
                validate_llm_decisions(
                    batch,
                    parsed
                )
            )


            if not valid:

                raise ValueError(
                    reason
                )


            for item in parsed["items"]:

                decisions[
                    item["id"]
                ] = {
                    "semantic_type": item[
                        "semantic_type"
                    ],
                    "level": item.get(
                        "level"
                    ),
                    "confidence": item.get(
                        "confidence"
                    ),
                    "source": "llm"
                }


            llm_batches_used += 1


        except Exception as error:

            # A batch has failed after every retry, or the
            # response could not be validated.
            #
            # Silently substituting heuristic output would
            # report success for a document the LLM never
            # actually structured. That is only allowed when
            # explicitly opted into.

            if not settings.STRUCTURE_ALLOW_FALLBACK:

                logger.error(
                    "LLM structuring failed on batch %d: %s",
                    batch_number,
                    error
                )

                raise StructuringError(
                    f"LLM structuring failed on batch "
                    f"{batch_number}: {error} "
                    "Set STRUCTURE_ALLOW_FALLBACK=true to "
                    "degrade to the deterministic classifier "
                    "instead of failing."
                ) from error


            fallback_batches += 1


            logger.warning(
                "LLM structuring failed on batch %d (%s). "
                "Falling back to the deterministic "
                "classifier for this batch "
                "(STRUCTURE_ALLOW_FALLBACK=true).",
                batch_number,
                error
            )


            fallback = (
                python_fallback_decisions(
                    batch
                )
            )


            decisions.update(
                fallback
            )


    return (
        decisions,
        {
            "enabled": True,
            "used": llm_batches_used > 0,
            "model": GROQ_MODEL,
            "llm_batches_used": (
                llm_batches_used
            ),
            "fallback_batches": (
                fallback_batches
            )
        }
    )


# ============================================================
# APPLY SEMANTIC DECISIONS
# ============================================================

def apply_semantic_decisions(
    blocks: List[Dict[str, Any]],
    decisions: Dict[
        str,
        Dict[str, Any]
    ]
) -> List[Dict[str, Any]]:
    """
    Apply LLM decisions WITHOUT modifying content.

    A paragraph may become a semantic heading.

    Its original text remains unchanged.
    """

    structured_blocks = []


    for block in blocks:

        new_block = dict(block)

        decision = decisions.get(
            block["id"]
        )


        if not decision:

            decision = {
                "semantic_type": (
                    "heading"
                    if block["type"] == "heading"
                    else "content"
                ),
                "level": block.get(
                    "python_heading_level",
                    None
                ),
                "confidence": 0.0,
                "source": "default"
            }


        # Tables always remain tables.

        if block["type"] == "table":

            new_block[
                "semantic_type"
            ] = "content"

            new_block[
                "semantic_level"
            ] = None


        else:

            new_block[
                "semantic_type"
            ] = decision[
                "semantic_type"
            ]


            new_block[
                "semantic_level"
            ] = (
                decision.get("level")
                if decision[
                    "semantic_type"
                ] == "heading"
                else None
            )


        new_block[
            "semantic_confidence"
        ] = decision.get(
            "confidence"
        )


        new_block[
            "semantic_source"
        ] = decision.get(
            "source"
        )


        structured_blocks.append(
            new_block
        )


    return structured_blocks


# ============================================================
# DOCUMENT TREE
# ============================================================

def create_section_node(
    title: str,
    level: int,
    source_block_id: str
) -> Dict[str, Any]:
    """
    Create document tree section node.
    """

    return {
        "type": "section",
        "title": title,
        "level": level,
        "source_block_id": source_block_id,
        "content": [],
        "children": []
    }


def build_document_tree(
    blocks: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Build hierarchical tree.

    IMPORTANT:

    The tree references the same preserved blocks.

    Content is never removed from the flat block list.
    """

    root = {
        "type": "document",
        "title": "Document",
        "content": [],
        "children": []
    }


    stack = [
        {
            "node": root,
            "level": 0
        }
    ]


    for block in blocks:


        # ----------------------------------------------------
        # SEMANTIC HEADING
        # ----------------------------------------------------

        if (
            block.get(
                "semantic_type"
            ) == "heading"
            and block["type"] != "table"
        ):

            level = block.get(
                "semantic_level"
            )

            if not isinstance(level, int):

                level = 1


            section = (
                create_section_node(
                    title=block.get(
                        "text",
                        ""
                    ),
                    level=level,
                    source_block_id=block["id"]
                )
            )


            while (
                len(stack) > 1
                and stack[-1]["level"] >= level
            ):

                stack.pop()


            parent = stack[-1]["node"]

            parent[
                "children"
            ].append(
                section
            )


            stack.append(
                {
                    "node": section,
                    "level": level
                }
            )

            continue


        # ----------------------------------------------------
        # NORMAL CONTENT
        # ----------------------------------------------------

        current_section = (
            stack[-1]["node"]
        )


        current_section[
            "content"
        ].append(
            block["id"]
        )


    return root


# ============================================================
# METADATA
# ============================================================

def extract_basic_metadata(
    blocks: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Extract basic metadata.

    Metadata extraction NEVER removes content.
    """

    metadata = {
        "standard_number": None,
        "document_title": None
    }


    all_text = []


    for block in blocks:

        if block["type"] in (
            "heading",
            "paragraph"
        ):

            text = block.get(
                "text",
                ""
            )

            if text:
                all_text.append(
                    text
                )


    joined_text = "\n".join(
        all_text
    )


    # --------------------------------------------------------
    # BIS / IS NUMBER
    # --------------------------------------------------------

    standard_match = re.search(
        r"\bIS\s+\d+"
        r"(?:\s*\([^)]+\))?"
        r"\s*:\s*\d{4}\b",
        joined_text,
        flags=re.IGNORECASE
    )


    if standard_match:

        metadata[
            "standard_number"
        ] = (
            standard_match
            .group(0)
            .strip()
        )


    # --------------------------------------------------------
    # DOCUMENT TITLE
    # --------------------------------------------------------

    for block in blocks:

        if (
            block.get(
                "semantic_type"
            ) == "heading"
        ):

            title = normalize_whitespace(
                block.get(
                    "text",
                    ""
                )
            )


            if not title:
                continue


            if re.match(
                r"^(ANNEX|APPENDIX|TABLE)\b",
                title,
                flags=re.IGNORECASE
            ):

                continue


            if len(title) >= 8:

                metadata[
                    "document_title"
                ] = title

                break


    return metadata


# ============================================================
# LOSSLESS VALIDATION
# ============================================================

def get_preserved_block_ids(
    blocks: List[Dict[str, Any]]
) -> List[str]:
    """
    Return all block IDs.
    """

    return [
        block["id"]
        for block in blocks
    ]


def validate_block_preservation(
    original_blocks: List[Dict[str, Any]],
    structured_blocks: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Strong structural preservation validation.

    This validates BLOCK preservation.

    Unlike simple token coverage, this ensures that
    every original Python block still exists.
    """

    original_ids = (
        get_preserved_block_ids(
            original_blocks
        )
    )

    structured_ids = (
        get_preserved_block_ids(
            structured_blocks
        )
    )


    original_set = set(
        original_ids
    )

    structured_set = set(
        structured_ids
    )


    missing_blocks = sorted(
        original_set - structured_set
    )


    extra_blocks = sorted(
        structured_set - original_set
    )


    duplicate_blocks = []


    for block_id in structured_set:

        if (
            structured_ids.count(
                block_id
            ) > 1
        ):

            duplicate_blocks.append(
                block_id
            )


    order_preserved = (
        original_ids == structured_ids
    )


    valid = (
        not missing_blocks
        and not extra_blocks
        and not duplicate_blocks
        and order_preserved
    )


    return {
        "valid": valid,
        "original_block_count": (
            len(original_blocks)
        ),
        "structured_block_count": (
            len(structured_blocks)
        ),
        "missing_blocks": (
            missing_blocks
        ),
        "extra_blocks": (
            extra_blocks
        ),
        "duplicate_blocks": (
            duplicate_blocks
        ),
        "order_preserved": (
            order_preserved
        )
    }


def get_all_structured_text(
    blocks: List[Dict[str, Any]]
) -> str:
    """
    Collect preserved text for token coverage validation.
    """

    parts = []


    for block in blocks:


        if block["type"] in (
            "heading",
            "paragraph"
        ):

            source_lines = block.get(
                "source_lines"
            )


            if source_lines:

                parts.extend(
                    source_lines
                )

            else:

                parts.append(
                    block.get(
                        "text",
                        ""
                    )
                )


        elif block["type"] == "table":

            parts.extend(
                block.get(
                    "raw_lines",
                    []
                )
            )


    return "\n".join(
        parts
    )


def calculate_token_coverage(
    original_text: str,
    structured_text: str
) -> Dict[str, Any]:
    """
    Secondary validation.

    Checks lexical preservation.
    """

    original_tokens = re.findall(
        r"\w+",
        original_text.lower()
    )


    structured_tokens = re.findall(
        r"\w+",
        structured_text.lower()
    )


    original_set = set(
        original_tokens
    )

    structured_set = set(
        structured_tokens
    )


    if not original_set:

        coverage = 1.0

    else:

        coverage = (
            len(
                original_set
                & structured_set
            )
            /
            len(original_set)
        )


    missing_tokens = sorted(
        original_set
        - structured_set
    )


    return {
        "original_token_count": (
            len(original_tokens)
        ),
        "structured_token_count": (
            len(structured_tokens)
        ),
        "unique_original_tokens": (
            len(original_set)
        ),
        "unique_structured_tokens": (
            len(structured_set)
        ),
        "token_coverage": round(
            coverage,
            6
        ),
        "sample_missing_tokens": (
            missing_tokens[:100]
        )
    }


# ============================================================
# MAIN STRUCTURING FUNCTION
# ============================================================

def structure_document(
    text: str
) -> Dict[str, Any]:
    """
    MAIN PIPELINE


        EXTRACTED DOCUMENT TEXT
                  |
                  v
        PYTHON BOUNDARY PARSING
                  |
                  v
        IMMUTABLE BLOCKS
                  |
                  v
        LLM SEMANTIC DECISIONS
                  |
                  v
        APPLY DECISIONS
        WITHOUT CHANGING CONTENT
                  |
                  v
        DOCUMENT TREE
                  |
                  v
        LOSSLESS VALIDATION
    """

    if not isinstance(text, str):

        raise TypeError(
            "structure_document expects a string."
        )


    original_text = text


    # --------------------------------------------------------
    # STEP 1
    # PYTHON BOUNDARIES
    # --------------------------------------------------------

    original_blocks = parse_blocks(
        original_text
    )


    # --------------------------------------------------------
    # STEP 2
    # LLM SEMANTIC STRUCTURE
    # --------------------------------------------------------

    decisions, llm_metadata = (
        semantic_structure(
            original_blocks
        )
    )


    # --------------------------------------------------------
    # STEP 3
    # APPLY DECISIONS
    # --------------------------------------------------------

    structured_blocks = (
        apply_semantic_decisions(
            original_blocks,
            decisions
        )
    )


    # --------------------------------------------------------
    # STEP 4
    # BUILD TREE
    # --------------------------------------------------------

    document_tree = (
        build_document_tree(
            structured_blocks
        )
    )


    # --------------------------------------------------------
    # STEP 5
    # METADATA
    # --------------------------------------------------------

    metadata = (
        extract_basic_metadata(
            structured_blocks
        )
    )


    # --------------------------------------------------------
    # STEP 6
    # VALIDATION
    # --------------------------------------------------------

    block_validation = (
        validate_block_preservation(
            original_blocks,
            structured_blocks
        )
    )


    structured_text = (
        get_all_structured_text(
            structured_blocks
        )
    )


    token_validation = (
        calculate_token_coverage(
            original_text,
            structured_text
        )
    )


    # --------------------------------------------------------
    # COUNTS
    # --------------------------------------------------------

    heading_count = sum(
        1
        for block in structured_blocks
        if block.get(
            "semantic_type"
        ) == "heading"
    )


    paragraph_count = sum(
        1
        for block in structured_blocks
        if block["type"] == "paragraph"
    )


    table_count = sum(
        1
        for block in structured_blocks
        if block["type"] == "table"
    )


    return {

        "metadata": metadata,

        "llm": llm_metadata,

        "blocks": structured_blocks,

        "document_tree": document_tree,

        "validation": {

            "block_count": (
                len(structured_blocks)
            ),

            "heading_count": (
                heading_count
            ),

            "paragraph_count": (
                paragraph_count
            ),

            "table_count": (
                table_count
            ),

            "block_preservation": (
                block_validation
            ),

            "token_preservation": (
                token_validation
            )

        },

        # RAW SOURCE IS ALWAYS KEPT.

        "raw_text": original_text
    }


# ============================================================
# FILE FUNCTIONS
# ============================================================

def read_input_file(
    input_path: str
) -> str:
    """
    Read input document.
    """

    path = Path(
        input_path
    )


    if not path.exists():

        raise FileNotFoundError(
            f"Input file not found: "
            f"{input_path}"
        )


    return path.read_text(
        encoding="utf-8",
        errors="replace"
    )


def write_output_file(
    data: Dict[str, Any],
    output_path: str
):
    """
    Write structured JSON.
    """

    path = Path(
        output_path
    )


    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )


    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def structured_blocks_to_markdown(blocks: List[Dict[str, Any]]) -> str:
    """
    Render structured blocks into valid Markdown.

    Canonical format between structuring -> normalization -> chunking is Markdown (.md).
    Semantic headings become Markdown headings with appropriate levels (#, ##, ...).
    Tables and paragraphs preserve all original text tokens.
    """
    parts = []
    for block in blocks:
        b_type = block.get("type")
        sem_type = block.get("semantic_type")

        if sem_type == "heading" or b_type == "heading":
            level = block.get("semantic_level") or block.get("python_heading_level") or 1
            try:
                level = max(1, min(6, int(level)))
            except (ValueError, TypeError):
                level = 1
            text = block.get("text", "").strip()
            text = re.sub(r"^#+\s*", "", text)
            parts.append(f"{'#' * level} {text}")
        elif b_type == "table":
            raw_lines = block.get("raw_lines")
            if raw_lines:
                parts.append("\n".join(raw_lines))
            elif "rows" in block and block["rows"]:
                rows = block["rows"]
                header = "| " + " | ".join(rows[0]) + " |"
                sep = "| " + " | ".join(["---"] * len(rows[0])) + " |"
                body = ["| " + " | ".join(r) + " |" for r in rows[1:]]
                parts.append("\n".join([header, sep] + body))
        else:
            source_lines = block.get("source_lines")
            if source_lines:
                parts.append("\n".join(source_lines))
            else:
                parts.append(block.get("text", ""))

    return "\n\n".join(p for p in parts if p.strip())


def structure_markdown_file(
    input_path: Path,
    output_path: Path,
    document_id: Optional[str] = None
) -> Path:
    """
    Process a cleaned Markdown file through semantic structuring and write output.

    Canonical format between structuring -> normalization -> chunking is Markdown (.md).
    If output_path has a .json extension, JSON is written for inspection/debugging.
    Otherwise, structured Markdown is written, and a sidecar .json file is also saved.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    text = input_path.read_text(encoding="utf-8", errors="replace")
    result = structure_document(text)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == ".json":
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    else:
        # Write canonical structured markdown
        markdown = structured_blocks_to_markdown(result["blocks"])
        output_path.write_text(markdown, encoding="utf-8")

        # Also write sidecar JSON for inspectability
        sidecar_path = output_path.with_suffix(".json")
        try:
            sidecar_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.debug("Failed to write sidecar JSON: %s", e)

    logger.info(
        "Structuring successful | file=%s | blocks=%d",
        output_path.name,
        len(result.get("blocks", []))
    )
    return output_path


# ============================================================
# ENVIRONMENT DISPLAY
# ============================================================

def print_environment():
    """
    Display environment configuration.

    Never print the API key itself.
    """

    print("\n" + "=" * 70)

    print("ENVIRONMENT")

    print("-" * 70)

    print(
        f"Project root : "
        f"{BASE_DIR}"
    )

    print(
        "GROQ key     : "
        + (
            "FOUND"
            if settings.GROQ_API_KEY
            else "NOT FOUND"
        )
    )

    print(
        f"Model        : "
        f"{GROQ_MODEL}"
    )

    print(
        f"LLM enabled  : "
        f"{settings.LLM_ENABLED}"
    )

    print("=" * 70)


# ============================================================
# COMMAND LINE
# ============================================================

def main():

    if len(sys.argv) < 2:

        print(
            "\nUsage:\n"
        )

        print(
            "    python structure.py "
            "input.md output.json\n"
        )

        print(
            "Example:\n"
        )

        print(
            "    python structure.py "
            "data/cleaned/document_cleaned.md "
            "data/structured/document_structured.json\n"
        )

        sys.exit(1)


    input_path = sys.argv[1]


    if len(sys.argv) >= 3:

        output_path = sys.argv[2]

    else:

        input_file = Path(
            input_path
        )

        output_path = str(
            input_file.with_suffix(
                ".structured.json"
            )
        )


    print_environment()


    print("\n" + "=" * 70)

    print(
        "LOSSLESS DOCUMENT STRUCTURING"
    )

    print("=" * 70)

    print(
        f"\nInput : {input_path}"
    )

    print(
        f"Output: {output_path}"
    )

    print(
        f"Model : {GROQ_MODEL}"
    )

    print(
        f"LLM   : {settings.LLM_ENABLED}"
    )


    # --------------------------------------------------------
    # STEP 1
    # --------------------------------------------------------

    print(
        "\n[1/5] Reading document..."
    )


    text = read_input_file(
        input_path
    )


    print(
        f"      Characters: "
        f"{len(text):,}"
    )


    # --------------------------------------------------------
    # STEP 2
    # --------------------------------------------------------

    print(
        "\n[2/5] Python boundary parsing..."
    )


    preview_blocks = parse_blocks(
        text
    )


    heading_candidates = sum(
        1
        for block in preview_blocks
        if block.get(
            "heading_candidate",
            False
        )
    )


    paragraphs = sum(
        1
        for block in preview_blocks
        if block["type"] == "paragraph"
    )


    tables = sum(
        1
        for block in preview_blocks
        if block["type"] == "table"
    )


    print(
        f"      Blocks: "
        f"{len(preview_blocks)}"
    )

    print(
        f"      Heading candidates: "
        f"{heading_candidates}"
    )

    print(
        f"      Paragraphs: "
        f"{paragraphs}"
    )

    print(
        f"      Tables: "
        f"{tables}"
    )


    # --------------------------------------------------------
    # STEP 3
    # --------------------------------------------------------

    print(
        "\n[3/5] Semantic structuring..."
    )


    print(
        "\n      Calling LLM "
        "for semantic structure..."
    )


    result = structure_document(
        text
    )


    llm_info = result["llm"]


    if llm_info.get("used"):

        print(
            f"\n      LLM used: "
            f"{llm_info['model']}"
        )

        print(
            f"      LLM batches: "
            f"{llm_info.get('llm_batches_used', 0)}"
        )

        print(
            f"      Fallback batches: "
            f"{llm_info.get('fallback_batches', 0)}"
        )

    else:

        print(
            "\n      Python fallback used."
        )


    # --------------------------------------------------------
    # STEP 4
    # --------------------------------------------------------

    print(
        "\n[4/5] Checking information preservation..."
    )


    validation = result[
        "validation"
    ]


    block_preservation = validation[
        "block_preservation"
    ]


    token_preservation = validation[
        "token_preservation"
    ]


    print(
        f"      Token coverage: "
        f"{token_preservation['token_coverage']:.2%}"
    )


    print(
        f"      Structure valid: "
        f"{block_preservation['valid']}"
    )


    print(
        f"      Missing blocks: "
        f"{len(block_preservation['missing_blocks'])}"
    )


    print(
        f"      Duplicate blocks: "
        f"{len(block_preservation['duplicate_blocks'])}"
    )


    if (
        token_preservation[
            "sample_missing_tokens"
        ]
    ):

        print(
            "      Sample missing tokens:"
        )

        print(
            "      "
            + ", ".join(
                token_preservation[
                    "sample_missing_tokens"
                ][:20]
            )
        )


    # --------------------------------------------------------
    # STEP 5
    # --------------------------------------------------------

    print(
        "\n[5/5] Writing structured document..."
    )


    if Path(output_path).suffix.lower() == ".md":
        markdown = structured_blocks_to_markdown(result["blocks"])
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(markdown, encoding="utf-8")
        try:
            write_output_file(result, str(Path(output_path).with_suffix(".json")))
        except Exception:
            pass
    else:
        write_output_file(
            result,
            output_path
        )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "DONE"
    )

    print(
        "=" * 70
    )


    print(
        "\nStructured document "
        "saved to:"
    )

    print(
        output_path
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
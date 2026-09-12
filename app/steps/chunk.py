"""
Step 5: Document Chunking

Input:
    Normalized Markdown content or file path

Process:
    Splits Markdown into logical chunks for embedding.
    Preserves heading context and section hierarchy.
    Handles tables and large sections intelligently.

Output:
    JSON file with chunk array

This module is designed to be generic and reusable:
    - Does NOT contain hardcoded paths
    - Processes ONE Markdown file at a time
    - Takes input_path and output_path as parameters
    - Creates parent directories if needed
    - Generates deterministic chunk IDs

Chunk metadata includes:
    - chunk_id: Unique identifier
    - document_id: Source document identifier
    - source_file: Relative path to source file
    - section: Section heading
    - heading_context: Hierarchy of headings
    - content: Chunk text content
    - character_count: Length of content
"""

import json
import logging
import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models import ChunkMetadata
from app.steps.bis_extractor import (
    extract_amendment_number,
    extract_edition,
    extract_part_number,
    extract_standard_number,
    extract_standard_title,
    extract_standard_year,
    compute_content_hash,
    PARSER_VERSION,
)


logger = logging.getLogger(__name__)


def extract_clause_id(heading: str) -> Optional[str]:
    """Extract clause number like '4.2.1' or 'Clause 3' from heading."""
    if not heading:
        return None
    m = re.match(r"^(?:Clause\s+|Section\s+)?(\d+(?:\.\d+)*)", heading.strip(), re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def load_docling_provenance(prov_path: Optional[Path]) -> Optional[Dict[str, Any]]:
    """
    Load native Docling structured provenance sidecar.

    Source Hierarchy:
    - Primary (Authoritative): Docling native item bounding boxes and page numbers
      extracted during DocumentConverter.convert() and recorded in .prov.json.
    - Fallback: <!-- PAGE N --> textual markers embedded in exported markdown.
    """
    if not prov_path:
        return None
    path_obj = Path(prov_path)
    if not path_obj.exists():
        return None
    try:
        with open(path_obj, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.debug("Could not read Docling provenance sidecar: %s", e)
        return None



# ============================================================
# CONFIGURATION
#
# Sourced from app.config so that chunk sizing is tunable
# through .env instead of by editing this module.
# ============================================================

# Maximum preferred chunk size (characters).
# Tables and structured blocks are preserved where possible.
MAX_CHUNK_CHARS = settings.MAX_CHUNK_CHARS

# Very small sections are merged with neighbouring sections.
MIN_CHUNK_CHARS = settings.MIN_CHUNK_CHARS


# ============================================================
# ERROR HANDLING
# ============================================================

class ChunkingError(Exception):
    """Raised when chunking fails."""


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def is_heading(line: str) -> bool:
    """
    Returns True if a line is a valid Markdown heading.

    Correct pattern: 1-6 # characters, followed by space and text.
    Page markers are explicitly excluded.
    """
    if "<!--" in line and re.search(r"<!--\s*PAGE\s+\d+", line, re.IGNORECASE):
        return False
    return bool(re.match(r"^\s{0,3}#{1,6}\s+\S", line))


def get_heading_level(line: str) -> int:
    """
    Extract Markdown heading level (1-6).

    Returns 0 if line is not a heading.
    """
    match = re.match(r"^\s*(#{1,6})\s+", line)
    return len(match.group(1)) if match else 0


def get_heading_text(line: str) -> str:
    """
    Extract heading text without Markdown markers.
    """
    return re.sub(r"^\s*#{1,6}\s+", "", line).strip()


def is_table_line(line: str) -> bool:
    """
    Detect if line is a Markdown table row.
    """
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|")


def is_blank(line: str) -> bool:
    """
    Check if a line is blank.
    """
    return not line.strip()


def generate_deterministic_chunk_id(
    document_id: str,
    section: str,
    content: str,
    occurrence: int = 0
) -> str:
    """
    Generate a deterministic, content-addressed chunk ID.

    The ID is a SHA-256 digest over (document_id, section,
    content). Position in the document is deliberately NOT part
    of the hash.

    Why content-based and not positional
    ------------------------------------
    With a positional ID (`..._chunk_00007`), inserting one
    paragraph near the top of a document renumbers every chunk
    after it. Re-embedding then writes a full set of new rows
    and orphans the old ones, because Chroma upsert matches on
    ID.

    With a content-based ID a chunk keeps its identity for as
    long as its own text and section are unchanged, no matter
    what changed earlier in the document. Re-ingestion becomes a
    real upsert: unchanged chunks overwrite themselves, only
    genuinely changed chunks appear as new IDs.

    Parameters
    ----------
    document_id:
        Included so identical boilerplate in two different
        standards does not collapse into one chunk.

    section:
        The section heading. Included so identical text under
        two different clauses stays distinguishable.

    content:
        The chunk body.

    occurrence:
        Disambiguator for the rare case where the same
        (document_id, section, content) triple genuinely repeats
        within one document. 0 for the first occurrence. Because
        it counts occurrences in document order rather than
        absolute position, it stays stable across runs.
    """
    payload = f"{document_id}::{section.strip()}::{content.strip()}".encode("utf-8")
    content_hash = hashlib.sha256(payload).hexdigest()[:16]
    if occurrence > 0:
        return f"{document_id}_{content_hash}_{occurrence}"
    return f"{document_id}_{content_hash}"


# ============================================================
# MARKDOWN PARSING
# ============================================================

def parse_markdown_sections(
    markdown_content: str
) -> List[Dict[str, Any]]:
    """
    Parse Markdown into logical sections.

    Returns list of sections with:
        - heading: Section heading text
        - heading_level: Markdown heading level (1-6)
        - heading_context: List of ancestor headings
        - content: Section content as text

    All information is preserved. No content is removed.
    """

    lines = markdown_content.splitlines()
    sections = []
    heading_stack = []
    current_section = None
    current_page = None
    page_marker_re = re.compile(r"^\s*(?:#{1,6}\s+)?<!--\s*PAGE\s+(\d+)\s*-->", re.IGNORECASE)

    def save_current_section():
        nonlocal current_section
        if current_section is None:
            return

        content = "\n".join(current_section["lines"]).strip()

        if content:
            p_start = current_section.get("page_start") or current_section.get("page_number")
            p_end = current_section.get("page_end") or p_start
            sections.append({
                "heading": current_section["heading"],
                "heading_level": current_section["heading_level"],
                "heading_context": current_section["heading_context"],
                "page_number": p_start,
                "page_start": p_start,
                "page_end": p_end,
                "content": content
            })

        current_section = None

    for line in lines:
        page_m = page_marker_re.match(line.strip())
        if page_m:
            current_page = int(page_m.group(1))
            continue

        if is_heading(line):

            save_current_section()

            level = get_heading_level(line)
            heading_text = get_heading_text(line)

            # Remove headings at same or deeper level
            while heading_stack and heading_stack[-1]["level"] >= level:
                heading_stack.pop()

            heading_stack.append({
                "level": level,
                "text": heading_text
            })

            heading_context = [item["text"] for item in heading_stack]

            current_section = {
                "heading": heading_text,
                "heading_level": level,
                "heading_context": heading_context,
                "page_number": current_page,
                "page_start": current_page,
                "page_end": current_page,
                "lines": [line]
            }

        else:

            # Content before first heading
            if current_section is None:
                if line.strip():
                    current_section = {
                        "heading": "Preamble",
                        "heading_level": 0,
                        "heading_context": [],
                        "page_number": current_page,
                        "page_start": current_page,
                        "page_end": current_page,
                        "lines": []
                    }
                else:
                    continue

            current_section["lines"].append(line)
            if line.strip() and current_page is not None:
                current_section["page_end"] = current_page

    save_current_section()
    return sections


# ============================================================
# SECTION MERGING
# ============================================================

def is_heading_only_section(section: Dict[str, Any]) -> bool:
    """
    Check if section contains only its heading.
    """
    content_lines = [
        line.strip()
        for line in section["content"].splitlines()
        if line.strip()
    ]
    return (
        len(content_lines) == 1
        and is_heading(content_lines[0])
    )


def merge_sections(
    sections: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Merge logically connected small sections.

    Rules:
        1. Heading-only sections merge with next section
        2. Tiny sections merge with related next section
        3. Large sections remain independent
        4. No content is deleted
    """

    merged = []
    index = 0

    while index < len(sections):

        current = sections[index]
        current_size = len(current["content"])

        # RULE 1: Merge heading-only sections
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

            merged.append({
                "heading": next_section["heading"],
                "heading_level": next_section["heading_level"],
                "heading_context": next_section["heading_context"],
                "page_number": current.get("page_number") or next_section.get("page_number"),
                "content": combined_content
            })

            index += 2
            continue

        # RULE 2: Merge very small sections
        if (
            current_size < MIN_CHUNK_CHARS
            and index + 1 < len(sections)
        ):

            next_section = sections[index + 1]
            combined_size = current_size + len(next_section["content"])

            # Only merge if result stays reasonable
            if combined_size <= MAX_CHUNK_CHARS:

                combined_content = (
                    current["content"].rstrip()
                    + "\n\n"
                    + next_section["content"].lstrip()
                )

                merged.append({
                    "heading": current["heading"],
                    "heading_level": current["heading_level"],
                    "heading_context": current["heading_context"],
                    "page_number": current.get("page_number") or next_section.get("page_number"),
                    "content": combined_content
                })

                index += 2
                continue

        # DEFAULT: Keep unchanged
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
    Split genuinely large sections.

    Priority:
        1. Preserve tables
        2. Split on paragraph boundaries
        3. Never remove content
    """

    content = section["content"]

    if len(content) <= MAX_CHUNK_CHARS:
        return [section]

    lines = content.splitlines()
    chunks = []
    current_lines = []
    current_length = 0

    def save_chunk():
        nonlocal current_lines, current_length

        chunk_text = "\n".join(current_lines).strip()

        if chunk_text:
            chunks.append({
                "heading": section["heading"],
                "heading_level": section["heading_level"],
                "heading_context": section["heading_context"],
                "page_number": section.get("page_number"),
                "content": chunk_text
            })

        current_lines = []
        current_length = 0

    index = 0

    while index < len(lines):

        line = lines[index]
        line_length = len(line) + 1

        # Detect complete table block
        if is_table_line(line):

            table_lines = []

            while (
                index < len(lines)
                and is_table_line(lines[index])
            ):
                table_lines.append(lines[index])
                index += 1

            table_text = "\n".join(table_lines)
            table_length = len(table_text)

            # If table fits with current content
            if current_length + table_length <= MAX_CHUNK_CHARS:

                current_lines.extend(table_lines)
                current_length += table_length

            else:

                # Save previous text first
                save_chunk()

                # If table itself fits, keep it
                if table_length <= MAX_CHUNK_CHARS:

                    current_lines.extend(table_lines)
                    current_length = table_length

                else:

                    # Split very large table
                    split_table(
                        table_lines,
                        section,
                        chunks
                    )

            continue

        # Normal line processing
        if (
            current_length + line_length > MAX_CHUNK_CHARS
            and current_lines
        ):
            save_chunk()

        current_lines.append(line)
        current_length += line_length
        index += 1

    save_chunk()
    return chunks


def split_table(
    table_lines: List[str],
    section: Dict[str, Any],
    chunks: List[Dict[str, Any]]
):
    """
    Split a very large Markdown table.

    Header and separator rows are repeated in each chunk
    so every chunk remains understandable.
    """

    if len(table_lines) < 3:
        chunks.append({
            "heading": section["heading"],
            "heading_level": section["heading_level"],
            "heading_context": section["heading_context"],
            "page_number": section.get("page_number"),
            "content": "\n".join(table_lines)
        })
        return

    header = table_lines[0]
    separator = table_lines[1]
    rows = table_lines[2:]

    current_lines = [header, separator]
    current_length = len("\n".join(current_lines))

    for row in rows:

        row_length = len(row) + 1

        if (
            current_length + row_length > MAX_CHUNK_CHARS
            and len(current_lines) > 2
        ):

            chunks.append({
                "heading": section["heading"],
                "heading_level": section["heading_level"],
                "heading_context": section["heading_context"],
                "page_number": section.get("page_number"),
                "content": "\n".join(current_lines)
            })

            current_lines = [header, separator]
            current_length = len("\n".join(current_lines))

        current_lines.append(row)
        current_length += row_length

    if len(current_lines) > 2:
        chunks.append({
            "heading": section["heading"],
            "heading_level": section["heading_level"],
            "heading_context": section["heading_context"],
            "page_number": section.get("page_number"),
            "content": "\n".join(current_lines)
        })


# ============================================================
# CREATE CHUNKS
# ============================================================

def create_chunks(
    markdown_content: str,
    document_id: str,
    source_file: str,
    doc_metadata=None,  # Optional[DocumentMetadata] — avoids circular import at type level
    docling_prov: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Complete chunking pipeline.

    Source Hierarchy:
        1. Docling Native Provenance (when docling_prov is supplied)
        2. Markdown Page Markers (<!-- PAGE N -->) as fallback

    Processes:
        1. Parse sections
        2. Merge small/heading-only sections
        3. Split large sections
        4. Create final chunks with metadata

    Args:
        markdown_content: Normalized Markdown content.
        document_id: Document identifier.
        source_file: Relative path to source file.
        doc_metadata: Optional DocumentMetadata built by the pipeline.

    Returns:
        List of chunk dictionaries.
    """

    logger.info(
        "Starting chunking | document_id=%s | size=%s chars",
        document_id,
        len(markdown_content)
    )

    # Step 1: Parse sections
    sections = parse_markdown_sections(markdown_content)
    logger.info("Sections found: %s", len(sections))

    # Step 2: Merge small sections
    sections = merge_sections(sections)
    logger.info("Sections after merging: %s", len(sections))

    # Step 3: Split large sections
    final_sections = []
    for section in sections:
        split_sections = split_large_section(section)
        final_sections.extend(split_sections)

    logger.info("Final sections: %s", len(final_sections))

    # Extract document-level BIS identifiers using bis_extractor
    standard_number = extract_standard_number(markdown_content)
    standard_year = extract_standard_year(markdown_content)
    part = extract_part_number(markdown_content)
    standard_title = extract_standard_title(markdown_content)
    amendment_number = extract_amendment_number(markdown_content)
    edition_or_version = extract_edition(markdown_content)

    # Propagate fields from doc_metadata if provided and extraction missed them
    if doc_metadata is not None:
        standard_number = standard_number or doc_metadata.standard_number
        standard_year = standard_year or doc_metadata.standard_year
        part = part or doc_metadata.part_number
        standard_title = standard_title or doc_metadata.standard_title
        amendment_number = amendment_number or doc_metadata.amendment_number
        edition_or_version = edition_or_version or doc_metadata.edition_or_version

    # Resolve doc-level provenance for chunk annotation
    doc_type = doc_metadata.document_type if doc_metadata else None
    source_hash = doc_metadata.source_hash if doc_metadata else None

    # Step 4: Create chunks with metadata conforming to ChunkMetadata contract
    chunks = []
    seen_hashes: Dict[str, int] = {}

    for section in final_sections:
        content = section["content"].strip()
        if not content:
            continue

        raw_id = generate_deterministic_chunk_id(
            document_id,
            section["heading"],
            content
        )
        count = seen_hashes.get(raw_id, 0)
        seen_hashes[raw_id] = count + 1

        if count > 0:
            chunk_id = generate_deterministic_chunk_id(
                document_id,
                section["heading"],
                content,
                occurrence=count
            )
        else:
            chunk_id = raw_id

        clause_id = extract_clause_id(section["heading"])
        page_no = section.get("page_number")

        offset = markdown_content.find(content)
        char_offset_start = offset if offset != -1 else None
        char_offset_end = (offset + len(content)) if offset != -1 else None

        # Phase 2 provenance fields
        content_hash = compute_content_hash(content)
        chunk_index = len(chunks)  # ordinal position in document
        clause_title = get_heading_text(section["heading"]) if section.get("heading") else None
        # page_start / page_end preserved from section tracking
        page_start = section.get("page_start") or page_no
        page_end = section.get("page_end") or page_start

        meta_obj = ChunkMetadata(
            chunk_id=chunk_id,
            document_id=document_id,
            source_file=source_file,
            section=section["heading"],
            heading_context=section.get("heading_context") or [],
            standard_number=standard_number,
            standard_year=standard_year,
            part=part,
            clause_id=clause_id,
            page_number=page_no,
            effective_date=None,
            amendment=None,
            is_current=None,
            char_offset_start=char_offset_start,
            char_offset_end=char_offset_end,
            # Phase 2
            page_start=page_start,
            page_end=page_end,
            content_hash=content_hash,
            chunk_index=chunk_index,
            standard_title=standard_title,
            clause_title=clause_title,
            part_number=part,
            amendment_number=amendment_number,
            edition_or_version=edition_or_version,
            publication_date=None,
            withdrawal_date=None,
            authority='BIS' if standard_number else None,
            document_type=doc_type,
            source_url=None,
            source_hash=source_hash,
            parser_version=PARSER_VERSION,
        )

        chunk_dict = {
            "chunk_id": chunk_id,
            "document_id": document_id,
            "source_file": source_file,
            "section": section["heading"],
            "heading_context": section.get("heading_context") or [],
            "standard_number": standard_number,
            "standard_year": standard_year,
            "part": part,
            "clause_id": clause_id,
            "page_number": page_no,
            "effective_date": None,
            "amendment": None,
            "is_current": None,
            "char_offset_start": char_offset_start,
            "char_offset_end": char_offset_end,
            # Phase 2
            "page_start": page_start,
            "page_end": page_end,
            "content_hash": content_hash,
            "chunk_index": chunk_index,
            "standard_title": standard_title,
            "clause_title": clause_title,
            "part_number": part,
            "amendment_number": amendment_number,
            "edition_or_version": edition_or_version,
            "publication_date": None,
            "withdrawal_date": None,
            "authority": 'BIS' if standard_number else None,
            "document_type": doc_type,
            "source_url": None,
            "source_hash": source_hash,
            "parser_version": PARSER_VERSION,
            "content": content,
            "character_count": len(content),
            "metadata": meta_obj.model_dump()
        }
        chunks.append(chunk_dict)

    logger.info("Created %s chunks", len(chunks))

    return chunks


# ============================================================
# CHUNK FILE
# ============================================================

def chunk_markdown_file(
    input_path: Path,
    output_path: Path,
    document_id: Optional[str] = None,
    source_file: Optional[str] = None,
    doc_metadata=None  # Optional[DocumentMetadata]
) -> Path:
    """
    Read, chunk, and save a Markdown file.

    This function:
        - Processes exactly one Markdown file
        - Creates output parent directory if needed
        - Saves chunks as JSON to output_path
        - Returns the output_path

    Args:
        input_path: Path to normalized Markdown file.
        output_path: Path where chunk JSON will be saved.
        document_id: Optional document identifier. If not provided,
                     extracted from filename stem.
        source_file: Optional source file relative path. If not provided,
                     uses filename.

    Returns:
        Path to the saved chunk JSON file.

    Raises:
        ChunkingError: If chunking fails.
        FileNotFoundError: If input file not found.
    """

    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        raise FileNotFoundError(
            f"Markdown file not found: {input_path}"
        )

    if not document_id:
        document_id = input_path.stem

    if not source_file:
        source_file = input_path.name

    logger.info(
        "Chunking Markdown file | file=%s | document_id=%s",
        input_path.name,
        document_id
    )

    try:

        # Read Markdown
        markdown_content = input_path.read_text(encoding="utf-8")

        if not markdown_content.strip():
            raise ChunkingError(
                f"Input file is empty: {input_path.name}"
            )

        # Look for authoritative Docling provenance sidecar
        candidate_prov = input_path.with_suffix(".prov.json")
        if not candidate_prov.exists():
            stem_clean = input_path.stem.replace("_normalized", "").replace("_cleaned", "").replace("_structured", "")
            from app.config import MARKDOWN_DATA_DIR
            alt_prov = MARKDOWN_DATA_DIR / f"{stem_clean}.prov.json"
            if alt_prov.exists():
                candidate_prov = alt_prov

        docling_prov = load_docling_provenance(candidate_prov)
        if docling_prov:
            logger.info("Authoritative Docling provenance sidecar loaded (%d items)", len(docling_prov.get("items", [])))

        # Create chunks
        chunks = create_chunks(
            markdown_content,
            document_id,
            source_file,
            doc_metadata=doc_metadata,
            docling_prov=docling_prov
        )

        # Prepare output data
        output_data = {
            "document_id": document_id,
            "source_file": source_file,
            "total_chunks": len(chunks),
            "chunks": chunks
        }

        # Create output directory
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Save as JSON
        output_path.write_text(
            json.dumps(output_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        logger.info(
            "Chunking successful | "
            "file=%s | chunks=%s",
            output_path.name,
            len(chunks)
        )

        return output_path

    except ChunkingError:
        raise

    except Exception as error:
        logger.exception(
            "Chunking failed | file=%s",
            input_path.name
        )
        raise ChunkingError(
            f"Failed to chunk Markdown file '{input_path.name}': {str(error)}"
        ) from error

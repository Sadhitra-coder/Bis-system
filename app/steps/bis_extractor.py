"""
app/steps/bis_extractor.py

Conservative BIS-specific metadata extraction from document text.
Never fabricates values. Returns None for uncertain fields.
"""
import hashlib
import re
import time
from typing import Optional

from app.models import DocumentMetadata

PARSER_VERSION = '2.0'

# IS 1234 : 2020 or IS 1234-1 : 2020 or IS 1234 (Part 1) : 2020
_IS_PATTERN = re.compile(
    r'\bIS(?:\s+No\.?)?\s+(\d+)'
    r'(?:\s*(?:-|\(Part\s*)\s*(\d+)\)?)?'
    r'(?:\s*:\s*(\d{4}))?',
    re.IGNORECASE
)

# Amendment: AMD 1, Amendment 2, Amd. 3, Amendment No. 2
_AMD_PATTERN = re.compile(
    r'\b(?:Amendment|Amd\.?)(?:\s+No\.?)?\s*(\d+)',
    re.IGNORECASE
)

# Edition: First Edition, 2nd Edition, Edition 3
_EDITION_PATTERN = re.compile(
    r'\b(First|Second|Third|Fourth|Fifth|Sixth|\d+(?:st|nd|rd|th)?)\s+Edition',
    re.IGNORECASE
)

# Publication/Effective date patterns
_DATE_PATTERN = re.compile(
    r'(?:published|effective|dated?)[:\s]+'
    r'(\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{4}|\d{4}-\d{2}-\d{2})',
    re.IGNORECASE
)


def extract_standard_number(text: str) -> Optional[str]:
    """Return 'IS XXXX' or 'IS XXXX-Part' string, or None."""
    m = _IS_PATTERN.search(text)
    if m:
        num = m.group(1)
        part = m.group(2)
        if part:
            return f'IS {num}-{part}'
        return f'IS {num}'
    return None


def extract_standard_year(text: str) -> Optional[int]:
    """Return the 4-digit year from the first IS pattern match, or None."""
    m = _IS_PATTERN.search(text)
    if m and m.group(3):
        try:
            return int(m.group(3))
        except ValueError:
            return None
    return None


def extract_part_number(text: str) -> Optional[str]:
    """Return the part digit string from IS XXXX-N or IS XXXX (Part N), or None."""
    m = _IS_PATTERN.search(text)
    if m and m.group(2):
        return m.group(2)
    return None


def extract_amendment_number(text: str) -> Optional[str]:
    """Return amendment number string (e.g. '1'), or None."""
    m = _AMD_PATTERN.search(text)
    return m.group(1) if m else None


def extract_edition(text: str) -> Optional[str]:
    """Return edition string (e.g. 'Second Edition'), or None."""
    m = _EDITION_PATTERN.search(text)
    return m.group(0).strip() if m else None


def extract_publication_date(text: str) -> Optional[str]:
    """Return a publication/effective date string found near keywords, or None."""
    m = _DATE_PATTERN.search(text)
    return m.group(1).strip() if m else None


def infer_document_type(text: str, filename: str) -> Optional[str]:
    """Infer document type conservatively from explicit signals only."""
    txt_lower = text[:2000].lower()  # check just the header region
    fname_lower = filename.lower()
    if 'amendment' in txt_lower or 'amd' in fname_lower:
        return 'amendment'
    if 'indian standard' in txt_lower or fname_lower.startswith('is'):
        return 'indian_standard'
    if 'guideline' in txt_lower:
        return 'guideline'
    if 'draft' in txt_lower:
        return 'draft'
    return None


def extract_standard_title(text: str) -> Optional[str]:
    """
    Extract the document title / standard title.
    Looks for the first H1 heading or a line after 'TITLE:' near the top.
    Returns None if uncertain.
    """
    lines = text.splitlines()
    for line in lines[:40]:  # only in first 40 lines
        stripped = line.strip()
        if stripped.startswith('# ') and len(stripped) > 3:
            candidate = stripped[2:].strip()
            if not re.search(r'<!--\s*PAGE\s+\d+', candidate, re.IGNORECASE):
                return candidate
        if stripped.upper().startswith('TITLE:'):
            tail = stripped[6:].strip()
            return tail or None
    return None


def derive_document_id(source_hash: str) -> str:
    """
    Derive deterministic canonical document identity from exact source bytes hash.

    Semantics:
    1. Identical source bytes (identical SHA-256 source_hash) always produce the exact same
       document identity, ensuring idempotency across repeated ingestions.
    2. Different source bytes (different SHA-256 source_hash) always produce distinct
       document identities, even if original filenames are identical.
    3. The original filename is preserved purely as human metadata (source_filename, source_file),
       never as trusted identity.
    """
    if not source_hash:
        raise ValueError("source_hash must be provided to derive document identity")
    return f"doc_{source_hash[:16]}"


def compute_content_hash(content: str) -> str:
    """SHA-256 of content string (UTF-8), truncated to 32 hex chars."""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()[:32]


def build_document_metadata(
    source_file: str = "",
    source_filename: str = "",
    source_hash: Optional[str] = None,
    text_sample: str = "",
    document_id: Optional[str] = None,
    **kwargs,
) -> DocumentMetadata:
    """
    Build a DocumentMetadata from the extracted markdown header text.
    Never fabricates. All Optional fields left None if not found.
    """
    if not document_id:
        if source_hash:
            document_id = derive_document_id(source_hash)
        else:
            document_id = f"doc_{hashlib.sha256((text_sample or source_filename).encode('utf-8')).hexdigest()[:16]}"

    return DocumentMetadata(
        document_id=document_id,
        source_file=source_file,
        source_filename=source_filename,
        source_hash=source_hash,
        document_title=extract_standard_title(text_sample),
        document_type=infer_document_type(text_sample, source_filename),
        authority='BIS' if _IS_PATTERN.search(text_sample) else None,
        standard_number=extract_standard_number(text_sample),
        standard_title=extract_standard_title(text_sample),  # same as document_title for IS docs
        standard_year=extract_standard_year(text_sample),
        edition_or_version=extract_edition(text_sample),
        part_number=extract_part_number(text_sample),
        amendment_number=extract_amendment_number(text_sample),
        publication_date=extract_publication_date(text_sample),
        effective_date=None,
        withdrawal_date=None,
        is_current=None,
        source_url=None,
        ingestion_timestamp=time.time(),
        parser_version=PARSER_VERSION,
    )

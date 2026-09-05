from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse, urlunparse


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_text(text: str | None) -> str:
    if not text:
        return ""

    text = str(text)

    text = text.replace("\xa0", " ")

    # Remove zero-width / directional characters
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e\ufeff]", "", text)

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def limit_text(
    value: str | None,
    max_length: int,
) -> str | None:

    if value is None:
        return None

    value = normalize_text(value)

    if not value:
        return None

    return value[:max_length]


# ============================================================
# URL
# ============================================================

def get_absolute_url(
    url: str,
    base_url: str,
) -> str:

    return urljoin(base_url, url)


def canonicalize_url(url: str) -> str:

    if not url:
        return ""

    parsed = urlparse(url)

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    path = parsed.path or "/"

    if path != "/":
        path = path.rstrip("/")

    return urlunparse(
        (
            scheme,
            netloc,
            path,
            "",
            parsed.query,
            "",
        )
    )


def is_pdf_candidate(
    url: str,
    content_type: str | None = None,
) -> bool:

    if not url:
        return False

    clean_url = url.lower().split("?")[0]

    if clean_url.endswith(".pdf"):
        return True

    if content_type:
        return "application/pdf" in content_type.lower()

    return False


# ============================================================
# FILENAME / TITLE
# ============================================================

def clean_filename(filename: str) -> str:

    filename = filename.rsplit("/", 1)[-1]

    filename = re.sub(
        r"\.pdf$",
        "",
        filename,
        flags=re.IGNORECASE,
    )

    filename = filename.replace("_", " ")
    filename = filename.replace("-", " ")

    filename = re.sub(r"\s+", " ", filename)

    return filename.strip()


def filename_to_title(url: str) -> str:

    parsed = urlparse(url)

    filename = parsed.path.rsplit("/", 1)[-1]

    return clean_filename(filename)


def extract_title(
    page_title: str | None,
    url: str | None,
) -> str:

    title = normalize_text(page_title)

    if title:
        return limit_text(title, 1024) or ""

    if url:
        return (
            limit_text(
                filename_to_title(url),
                1024,
            )
            or ""
        )

    return "Untitled Document"


# ============================================================
# STANDARD NUMBER
# ============================================================

STANDARD_PATTERNS = [

    # IS/ISO 12345
    re.compile(
        r"\bIS\s*/\s*ISO\s*[-/]?\s*"
        r"(\d{2,6})"
        r"(?:\s*[:/-]\s*(\d{4}))?\b",
        re.IGNORECASE,
    ),

    # INDIAN STANDARD IS 456
    re.compile(
        r"\bINDIAN\s+STANDARD\s+IS\s*[-/]?\s*"
        r"(\d{2,6})"
        r"(?:\s*[:/-]\s*(\d{4}))?\b",
        re.IGNORECASE,
    ),

    # IS 456
    # IS-456
    # IS/456
    re.compile(
        r"(?<![A-Z0-9])"
        r"IS\s*[-/]?\s*"
        r"(\d{2,6})"
        r"(?:\s*[:/-]\s*(\d{4}))?"
        r"(?!\d)",
        re.IGNORECASE,
    ),
]


def extract_standard_number(
    text: str | None,
) -> str | None:

    if not text:
        return None

    text = normalize_text(text)

    for pattern in STANDARD_PATTERNS:

        match = pattern.search(text)

        if not match:
            continue

        number = match.group(1)
        year = match.group(2)

        if year:
            return f"IS {number}:{year}"

        return f"IS {number}"

    return None


def extract_standard_number_from_sources(
    title: str | None = None,
    filename: str | None = None,
    url: str | None = None,
) -> str | None:

    # Highest confidence = title
    result = extract_standard_number(title)

    if result:
        return result

    # Second = filename
    result = extract_standard_number(filename)

    if result:
        return result

    # Third = URL
    result = extract_standard_number(url)

    if result:
        return result

    return None


# ============================================================
# REVISION
# ============================================================

def extract_revision(
    text: str | None,
) -> str | None:

    if not text:
        return None

    text = normalize_text(text)

    patterns = [

        re.compile(
            r"\b(?:revision|rev\.?)"
            r"\s*[:\-]?\s*(\d{1,3})\b",
            re.IGNORECASE,
        ),

        re.compile(
            r"\b(?:version|ver\.?)"
            r"\s*[:\-]?\s*(\d{1,3})\b",
            re.IGNORECASE,
        ),

        re.compile(
            r"\b(?:amendment|amd\.?)"
            r"\s*[:\-]?\s*(\d{1,3})\b",
            re.IGNORECASE,
        ),
    ]

    for pattern in patterns:

        match = pattern.search(text)

        if match:
            return match.group(1)

    return None


# ============================================================
# EDITION
# ============================================================

def extract_edition(
    text: str | None,
) -> str | None:

    if not text:
        return None

    text = normalize_text(text)

    patterns = [

        re.compile(
            r"\b(\d{1,3})(?:st|nd|rd|th)"
            r"\s+edition\b",
            re.IGNORECASE,
        ),

        re.compile(
            r"\bedition\s*[:\-]?\s*(\d{1,3})\b",
            re.IGNORECASE,
        ),

        re.compile(
            r"\bedn\.?\s*[:\-]?\s*(\d{1,3})\b",
            re.IGNORECASE,
        ),
    ]

    for pattern in patterns:

        match = pattern.search(text)

        if match:
            return match.group(1)

    return None


# ============================================================
# DOCUMENT TYPE
# ============================================================

def extract_document_type(
    text: str | None,
) -> str:

    value = normalize_text(text)

    if not value:
        return "Document"

    lower = value.lower()

    # Order matters.
    # More specific document types first.

    if re.search(r"\bamendment\b", lower):
        return "Amendment"

    if re.search(
        r"\bgazette\s+notification\b",
        lower,
    ):
        return "Gazette Notification"

    if re.search(
        r"\bannual\s+report\b",
        lower,
    ):
        return "Annual Report"

    if re.search(r"\bcircular\b", lower):
        return "Circular"

    if re.search(
        r"\bguidelines?\b",
        lower,
    ):
        return "Guideline"

    if re.search(r"\bscheme\b", lower):
        return "Scheme"

    if re.search(
        r"\bapplication\s+form\b",
        lower,
    ):
        return "Application"

    if re.search(
        r"\bapplication\b",
        lower,
    ):
        return "Application"

    if re.search(r"\bform\b", lower):
        return "Form"

    if re.search(
        r"\bnotification\b",
        lower,
    ):
        return "Notification"

    if re.search(r"\bbrochure\b", lower):
        return "Brochure"

    if re.search(
        r"\borganisation\b",
        lower,
    ):
        return "Organisation Document"

    if re.search(r"\bmanual\b", lower):
        return "Manual"

    return "Document"


# ============================================================
# CATEGORY
# ============================================================

def extract_category(
    text: str | None,
) -> str:

    value = normalize_text(text)

    if not value:
        return "General"

    lower = value.lower()

    if any(
        x in lower
        for x in [
            "testing laboratory",
            "testing laboratories",
            "laboratory",
            "calibration laboratory",
        ]
    ):
        return "Laboratory Services"

    if (
        "hallmark" in lower
        or "hallmarking" in lower
    ):
        return "Hallmarking"

    if (
        "product certification" in lower
        or "quality control order" in lower
        or re.search(r"\bqco\b", lower)
    ):
        return "Product Certification"

    if "scheme" in lower:
        return "Schemes"

    if any(
        x in lower
        for x in [
            "training",
            "internship",
            "course",
        ]
    ):
        return "Training"

    if (
        "consumer" in lower
        or "consumer affairs" in lower
    ):
        return "Consumer Affairs"

    if any(
        x in lower
        for x in [
            "international",
            "iso",
            "iec",
        ]
    ):
        return "International"

    if (
        "standard" in lower
        or "indian standard" in lower
    ):
        return "Standards"

    return "General"


# ============================================================
# SUBCATEGORY
# ============================================================

def extract_subcategory(
    text: str | None,
) -> str | None:

    value = normalize_text(text)

    if not value:
        return None

    lower = value.lower()

    if (
        "testing laboratory" in lower
        or "testing laboratories" in lower
    ):
        return "Testing Laboratories"

    if "calibration laboratory" in lower:
        return "Calibration Laboratories"

    if "hallmark" in lower:
        return "Hallmarking"

    if "product certification" in lower:
        return "Product Certification"

    if (
        "scheme of testing and inspection"
        in lower
    ):
        return "Scheme of Testing and Inspection"

    return None


# ============================================================
# KEYWORDS
# ============================================================

STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "dated",
    "document",
    "www",
}


def extract_keywords(
    text: str | None,
    max_keywords: int = 20,
) -> str | None:

    if not text:
        return None

    text = normalize_text(text).lower()

    words = re.findall(
        r"[a-zA-Z0-9]+",
        text,
    )

    keywords = []

    for word in words:

        if len(word) < 3:
            continue

        if word in STOP_WORDS:
            continue

        if word not in keywords:
            keywords.append(word)

        if len(keywords) >= max_keywords:
            break

    if not keywords:
        return None

    return ", ".join(keywords)


# ============================================================
# COMPLETE METADATA EXTRACTION
# ============================================================

def extract_metadata(
    *,
    title: str | None = None,
    url: str | None = None,
    description: str | None = None,
    filename: str | None = None,
    page_category: str | None = None,
    page_subcategory: str | None = None,
) -> dict:

    clean_title = extract_title(
        title,
        url,
    )

    clean_description = normalize_text(
        description
    )

    # --------------------------------------------------------
    # STANDARD NUMBER
    # --------------------------------------------------------
    #
    # DO NOT search arbitrary descriptions for IS 456.
    # This prevents false positives.
    #

    standard_number = (
        extract_standard_number_from_sources(
            title=clean_title,
            filename=filename,
            url=url,
        )
    )

    # --------------------------------------------------------
    # REVISION / EDITION
    # --------------------------------------------------------

    source_text = " ".join(
        x
        for x in [
            clean_title,
            filename or "",
            url or "",
        ]
        if x
    )

    revision = extract_revision(
        source_text
    )

    edition = extract_edition(
        source_text
    )

    # --------------------------------------------------------
    # DOCUMENT TYPE
    # --------------------------------------------------------

    type_source = " ".join(
        x
        for x in [
            clean_title,
            filename or "",
        ]
        if x
    )

    document_type = extract_document_type(
        type_source
    )

    # If we have a reliable IS number and
    # there isn't a more specific document type.
    if (
        standard_number
        and document_type == "Document"
    ):
        document_type = "Indian Standard"

    # --------------------------------------------------------
    # CATEGORY
    # --------------------------------------------------------

    category_source = " ".join(
        x
        for x in [
            clean_title,
            filename or "",
            page_category or "",
        ]
        if x
    )

    category = extract_category(
        category_source
    )

    # A detected IS number is strong evidence
    # for Standards.
    if standard_number:
        category = "Standards"

    # --------------------------------------------------------
    # SUBCATEGORY
    # --------------------------------------------------------

    subcategory_source = " ".join(
        x
        for x in [
            clean_title,
            filename or "",
            page_subcategory or "",
        ]
        if x
    )

    subcategory = extract_subcategory(
        subcategory_source
    )

    # --------------------------------------------------------
    # KEYWORDS
    # --------------------------------------------------------

    keywords_source = " ".join(
        x
        for x in [
            clean_title,
            clean_description,
            filename or "",
        ]
        if x
    )

    keywords = extract_keywords(
        keywords_source
    )

    return {
        "title": limit_text(
            clean_title,
            1024,
        ),

        "document_type": limit_text(
            document_type,
            128,
        ),

        "category": limit_text(
            category,
            255,
        ),

        "subcategory": limit_text(
            subcategory,
            255,
        ),

        "standard_number": limit_text(
            standard_number,
            128,
        ),

        "revision": limit_text(
            revision,
            64,
        ),

        "edition": limit_text(
            edition,
            64,
        ),

        "description": limit_text(
            clean_description,
            10000,
        ),

        "keywords": limit_text(
            keywords,
            4000,
        ),
    }
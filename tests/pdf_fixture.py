"""
Minimal PDF construction for integration tests.

WHY HAND-BUILT
--------------
The real ingestion path (Phase 6 section 17) must not be mocked away, so a
test needs a PDF that Docling can actually parse. No PDF writer is in the
dependency set, and adding one to run a test would be a production dependency
bought for a test fixture. The PDF format's uncompressed text form is small
enough to emit directly: a catalog, a pages tree, one page per text block, and
a content stream per page using the base-14 Helvetica font, which requires no
embedded font program.

Deliberately tiny. Docling runs a layout model over every page, so a fixture
page carries only the few lines a test asserts on.
"""

from pathlib import Path
from typing import List, Sequence


def _escape(text: str) -> str:
    """Escape the three characters that terminate a PDF string literal."""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream(lines: Sequence[str], font_size: int = 12) -> bytes:
    """Build a page content stream that draws `lines` top-down."""
    leading = font_size + 4
    parts = ["BT", f"/F1 {font_size} Tf", f"{leading} TL", "1 0 0 1 56 740 Tm"]
    for line in lines:
        parts.append(f"({_escape(line)}) Tj")
        parts.append("T*")
    parts.append("ET")
    return "\n".join(parts).encode("latin-1", errors="replace")


def build_pdf(pages: Sequence[Sequence[str]]) -> bytes:
    """
    Return the bytes of an uncompressed PDF containing `pages`.

    Each element of `pages` is the sequence of text lines on that page.
    Objects are emitted in a fixed order so the cross-reference table can be
    built from the recorded byte offsets, which is what makes the file valid
    rather than merely PDF-shaped.
    """
    if not pages:
        raise ValueError("A PDF needs at least one page.")

    n_pages = len(pages)
    # Object numbering: 1 catalog, 2 pages tree, 3 font,
    # then per page: a page object and its content stream.
    page_obj_ids = [4 + (2 * i) for i in range(n_pages)]
    stream_obj_ids = [5 + (2 * i) for i in range(n_pages)]

    objects: List[bytes] = []

    kids = " ".join(f"{oid} 0 R" for oid in page_obj_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode("latin-1")
    )
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )

    for i, lines in enumerate(pages):
        stream = _content_stream(lines)
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> "
                f"/Contents {stream_obj_ids[i]} 0 R >>"
            ).encode("latin-1")
        )
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
            + stream
            + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: List[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("latin-1")

    return bytes(out)


#: A one-page fixture standing in for a BIS standard. The identity lines are
#: what the metadata extractor reads, so they mirror a real title block.
BIS_STANDARD_PAGES: Sequence[Sequence[str]] = (
    (
        "INDIAN STANDARD",
        "IS 3055 : 2024",
        "SPECIFICATION FOR CLINICAL THERMOMETERS",
        "Third Edition",
        "",
        "4 REQUIREMENTS",
        "",
        "4.1 Calibration and Accuracy",
        "The maximum permissible error of temperature indication shall be",
        "plus or minus 0.1 degrees Celsius between 35.0 and 42.0 degrees",
        "Celsius. Calibration shall be conducted using an accredited",
        "circulating water bath traceable to national metrological standards.",
    ),
)


def write_bis_standard_pdf(path: Path) -> bytes:
    """Write the BIS fixture PDF to `path` and return its bytes."""
    data = build_pdf(BIS_STANDARD_PAGES)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data

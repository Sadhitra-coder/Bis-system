import pytest
from app.steps.clean import clean_markdown
from app.steps.normalize import normalize_markdown
from app.steps.chunk import (
    generate_deterministic_chunk_id,
    create_chunks,
)


def test_clean_markdown():
    raw_md = "Hello &amp; world!\u00a0\u200b\r\n\r\n\r\nLine 2   \n"
    cleaned = clean_markdown(raw_md)
    assert "Hello & world!" in cleaned
    assert "\u200b" not in cleaned
    assert "\r" not in cleaned


def test_normalize_markdown():
    escaped_pipe = r"| Column 1 \| Column 2 |"
    normalized = normalize_markdown(escaped_pipe)
    assert r"\|" not in normalized
    assert "|" in normalized


def test_deterministic_chunk_id():
    doc_id = "IS_3055"
    section = "Requirements"
    content = "The thermometer must meet the range 35C to 42C."

    id1 = generate_deterministic_chunk_id(doc_id, section, content)
    id2 = generate_deterministic_chunk_id(doc_id, section, content)

    # Identical content yields identical ID
    assert id1 == id2
    assert id1.startswith("IS_3055_")
    assert len(id1) == len("IS_3055_") + 16  # 16-hex prefix

    # Content change yields different ID
    id3 = generate_deterministic_chunk_id(doc_id, section, content + " Modified.")
    assert id1 != id3

    # Section change yields different ID
    id4 = generate_deterministic_chunk_id(doc_id, "Different Section", content)
    assert id1 != id4


def test_create_chunks():
    sample_text = """# Section 1
This is a standard paragraph with enough words to form valid chunks under the default character thresholds.

## Section 2
Another paragraph containing test data for chunking.
"""
    chunks = create_chunks(sample_text, document_id="doc1", source_file="doc1.pdf")
    assert len(chunks) >= 1
    first_chunk = chunks[0]
    assert "chunk_id" in first_chunk
    assert "content" in first_chunk
    assert first_chunk["document_id"] == "doc1"



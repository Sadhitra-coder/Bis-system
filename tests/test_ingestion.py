from app.crawler.metadata import canonicalize_url, extract_standard_number, is_pdf_candidate, make_deduplication_key


def test_canonicalize_url_normalizes_variants():
    source = "https://www.bis.gov.in/a/file.pdf?download=1"
    normalized = canonicalize_url(source)
    assert normalized.startswith("https://www.bis.gov.in/a/file.pdf")
    assert "download" not in normalized


def test_pdf_candidate_detection():
    assert is_pdf_candidate("https://www.bis.gov.in/a/file.pdf") is True
    assert is_pdf_candidate("https://www.bis.gov.in/standards/summary") is False


def test_deduplication_key_ignores_tracking_queries():
    key_a = make_deduplication_key("https://www.bis.gov.in/a/file.pdf?download=1")
    key_b = make_deduplication_key("https://www.bis.gov.in/a/file.pdf")
    assert key_a == key_b


def test_extract_standard_number_from_title_and_url():
    assert extract_standard_number("IS 1234 : 2024 Product Standard") == "IS 1234"
    assert extract_standard_number("https://www.bis.gov.in/standards/is1234.pdf") == "IS 1234"

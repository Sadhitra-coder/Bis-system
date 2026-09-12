import pytest
from app.steps.structure import (
    parse_blocks,
    python_fallback_decisions,
    structure_document,
    structured_blocks_to_markdown,
)


SAMPLE_MARKDOWN = """# IS 3055 : 2020
## CLINICAL THERMOMETERS - SPECIFICATION

1. SCOPE
This standard covers the requirements for clinical thermometers.

2. REQUIREMENTS
The thermometer shall meet the following requirements:

| Parameter | Limit |
| --- | --- |
| Accuracy | +/- 0.1 C |
| Range | 35 C to 42 C |

3. MARKING
Each thermometer shall be legibly and indelibly marked.
"""


def test_parse_blocks():
    blocks = parse_blocks(SAMPLE_MARKDOWN)
    assert len(blocks) >= 4
    types = [b["type"] for b in blocks]
    assert "heading" in types
    assert "paragraph" in types
    assert "table" in types


def test_structure_document_fallback():
    # With LLM_ENABLED=false, structure_document uses python_fallback_decisions
    result = structure_document(SAMPLE_MARKDOWN)
    assert "blocks" in result
    assert "validation" in result
    assert result["validation"]["block_count"] > 0
    assert result["validation"]["token_preservation"]["token_coverage"] >= 0.95
    assert result["metadata"]["standard_number"] == "IS 3055 : 2020"



def test_structured_blocks_to_markdown():
    result = structure_document(SAMPLE_MARKDOWN)
    md_output = structured_blocks_to_markdown(result["blocks"])
    assert "# CLINICAL THERMOMETERS" in md_output or "IS 3055" in md_output
    assert "Accuracy" in md_output
    assert "Range" in md_output


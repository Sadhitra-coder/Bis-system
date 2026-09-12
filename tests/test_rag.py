import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rag.retriever import normalize_scores, tokenize


def test_tokenize():
    tokens = tokenize("IS 3055: Clinical thermometers, Section 4.2-A")
    assert "is" in tokens
    assert "3055" in tokens
    assert "clinical" in tokens
    assert "thermometers" in tokens


def test_normalize_scores():
    # Empty
    assert len(normalize_scores([])) == 0

    # Constant
    res = normalize_scores([5.0, 5.0, 5.0])
    assert np.allclose(res, [1.0, 1.0, 1.0])

    # Normal range
    res = normalize_scores([0.0, 5.0, 10.0])
    assert np.isclose(res[0], 0.0)
    assert np.isclose(res[1], 0.5)
    assert np.isclose(res[2], 1.0)


def test_api_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_status():
    client = TestClient(app)
    response = client.get("/status")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "collection_name" in data
    assert "chunks_indexed" in data


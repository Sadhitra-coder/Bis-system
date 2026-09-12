"""
Shared pytest configuration.

The ingestion concurrency semaphore (app.api.upload._INGESTION_SLOTS) is
module level, so it is shared by every test in the session. A test that mocks
away `_ingest_background` acquires a permit that is never released, and the
leak accumulates until an unrelated later test receives a spurious 429. The
autouse fixture below hands each test its own semaphore so the limit is
tested where it is asserted and invisible everywhere else.
"""

import threading

import pytest


@pytest.fixture(autouse=True)
def fresh_ingestion_slots(monkeypatch):
    """Give every test a full, private set of ingestion permits."""
    from app.api import upload as upload_mod
    from app.config import settings

    monkeypatch.setattr(
        upload_mod,
        "_INGESTION_SLOTS",
        threading.BoundedSemaphore(settings.MAX_CONCURRENT_INGESTIONS),
    )

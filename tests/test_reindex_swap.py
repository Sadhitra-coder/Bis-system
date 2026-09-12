"""
Phase 6 section 10 — the rebuild's safety model, exercised for real.

WHAT THIS FILE EXISTS TO CATCH
------------------------------
The first real rebuild built a valid 1.6 MB index, validated it, and then died
on the promoting rename with `[WinError 5] Access is denied`. The rollback
worked and the live index survived, but the rebuild was unusable: it could
never finish. Nothing in the suite covered the swap, because every other test
either used an in-memory Chroma client or stopped at the write.

The failure was process-local. `SharedSystemClient.clear_system_cache()` reads
as a close and is not one -- its entire body assigns a new empty dict -- and the
collection is handed to `process_pdf`, which passes it further down the
pipeline, so `del` on the local name does not necessarily drop the last
reference. The store's files therefore stayed open, and Windows refuses to
rename a directory containing an open file.

So these tests use real persisted Chroma directories on disk and assert on the
filesystem, not on mocks. A mock cannot hold a file handle, which is the only
thing that made the bug possible.
"""

import gc
import os
from pathlib import Path

import chromadb
import pytest

from app.scripts.reindex import (
    SWAP_RENAME_ATTEMPTS,
    _release_client,
    _rename_with_retry,
    _swap_in,
)

COLLECTION = "swap_probe_collection"
DIM = 256


def _build_store(path: Path, count: int, tag: str):
    """
    Create a real persisted index big enough to have segment files.

    Size matters: a store holding a single tiny vector may never materialize
    the segment files whose open handles caused the original failure, so a
    one-document fixture can pass while the real thing fails.
    """
    client = chromadb.PersistentClient(path=str(path))
    collection = client.get_or_create_collection(name=COLLECTION)
    collection.add(
        ids=[f"{tag}_{i}" for i in range(count)],
        documents=[f"{tag} document {i}" for i in range(count)],
        embeddings=[[float(i % 11) + j * 0.001 for j in range(DIM)] for i in range(count)],
    )
    # A query forces the read path to open what it needs to open.
    collection.query(query_embeddings=[[0.5] * DIM], n_results=min(3, count))
    return client, collection


def _read_back(path: Path) -> list:
    """Open a store fresh and return its ids, then release it."""
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()
    client = chromadb.PersistentClient(path=str(path))
    ids = client.get_collection(name=COLLECTION).get()["ids"]
    _release_client(client)
    return ids


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------

def test_released_store_directory_can_be_renamed(tmp_path):
    """
    The regression itself: after release, the directory must move.

    An extra reference to the collection is kept deliberately and dropped
    after the release call, standing in for the reference `process_pdf` holds
    on the real path. If release depended on the caller having already dropped
    every reference, this is where that assumption breaks.
    """
    store = tmp_path / "store"
    client, collection = _build_store(store, 80, "rel")

    lingering = collection  # what the pipeline effectively holds
    _release_client(client)
    del lingering, collection, client
    gc.collect()

    target = tmp_path / "store_moved"
    os.rename(store, target)  # must not raise
    assert target.exists()
    assert not store.exists()


def test_release_is_safe_on_a_client_without_a_system():
    """
    A stub client must not make release explode.

    Release runs on the failure path of a rebuild as well as the success path,
    so an exception here would replace a useful error with a confusing one.
    """
    class Stub:
        pass

    _release_client(Stub())  # must not raise


# ---------------------------------------------------------------------------
# retrying rename
# ---------------------------------------------------------------------------

def test_rename_retry_recovers_from_a_transient_failure(tmp_path, monkeypatch):
    """A lock that clears within the retry window must not fail the swap."""
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"

    attempts = {"n": 0}
    real_rename = os.rename

    def flaky(a, b):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise PermissionError(5, "Access is denied")
        return real_rename(a, b)

    monkeypatch.setattr(os, "rename", flaky)
    monkeypatch.setattr("app.scripts.reindex.SWAP_RENAME_DELAY_SECONDS", 0.01)

    _rename_with_retry(src, dst, "test rename")

    assert attempts["n"] == 3
    assert dst.exists()


def test_rename_retry_gives_up_and_raises(tmp_path, monkeypatch):
    """
    A directory held open for real must still fail loudly.

    Retrying forever, or degrading to a copy, would trade a clear error for a
    non-atomic swap -- the exact thing the two-rename design avoids.
    """
    src = tmp_path / "src"
    src.mkdir()

    attempts = {"n": 0}

    def always_denied(a, b):
        attempts["n"] += 1
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(os, "rename", always_denied)
    monkeypatch.setattr("app.scripts.reindex.SWAP_RENAME_DELAY_SECONDS", 0.0)

    with pytest.raises(OSError):
        _rename_with_retry(src, tmp_path / "dst", "test rename")

    assert attempts["n"] == SWAP_RENAME_ATTEMPTS


# ---------------------------------------------------------------------------
# the swap
# ---------------------------------------------------------------------------

def test_swap_promotes_staging_and_preserves_the_displaced_index(tmp_path):
    """
    End to end on real directories: the live path gets the new store and the
    old one is kept, not deleted.

    Preserving rather than deleting is a deliberate asymmetry -- an operator
    can always remove a backup, but cannot recover one that was never made.
    """
    live = tmp_path / "vector_db"
    staging = tmp_path / "vector_db.rebuild"

    old_client, _ = _build_store(live, 10, "old")
    _release_client(old_client)
    del old_client

    new_client, new_collection = _build_store(staging, 90, "new")
    lingering = new_collection
    _release_client(new_client)
    del lingering, new_collection, new_client
    gc.collect()

    backup = _swap_in(staging, live)

    assert live.exists()
    assert not staging.exists()
    assert backup is not None and backup.exists()

    # The live path now serves the rebuilt content, read fresh from disk.
    live_ids = _read_back(live)
    assert len(live_ids) == 90
    assert all(i.startswith("new_") for i in live_ids)

    # And the displaced index is intact, not merely present.
    backup_ids = _read_back(backup)
    assert len(backup_ids) == 10
    assert all(i.startswith("old_") for i in backup_ids)


def test_swap_into_an_empty_location_needs_no_backup(tmp_path):
    """A first-ever build has nothing to displace and must not invent one."""
    live = tmp_path / "vector_db"
    staging = tmp_path / "vector_db.rebuild"

    client, collection = _build_store(staging, 12, "first")
    _release_client(client)
    del collection, client
    gc.collect()

    backup = _swap_in(staging, live)

    assert backup is None
    assert live.exists()
    assert len(_read_back(live)) == 12


def test_failed_promotion_restores_the_original_index(tmp_path, monkeypatch):
    """
    Section 10's core requirement: a swap that fails must leave the live index
    working, not half-replaced and not missing.

    The promotion is forced to fail after the displacement has already
    happened -- the exact window in which the live path does not exist. If the
    rollback were absent or wrong, the application would come up with no index
    at all and report the rebuild as the cause of something far more confusing.
    """
    live = tmp_path / "vector_db"
    staging = tmp_path / "vector_db.rebuild"

    old_client, _ = _build_store(live, 15, "old")
    _release_client(old_client)
    del old_client

    new_client, new_collection = _build_store(staging, 20, "new")
    _release_client(new_client)
    del new_collection, new_client
    gc.collect()

    real_rename = os.rename

    def fail_on_promotion(a, b):
        # Only the staging -> live rename fails; displacement and restore work.
        if Path(a) == staging:
            raise PermissionError(5, "Access is denied")
        return real_rename(a, b)

    monkeypatch.setattr(os, "rename", fail_on_promotion)
    monkeypatch.setattr("app.scripts.reindex.SWAP_RENAME_DELAY_SECONDS", 0.0)

    with pytest.raises(OSError):
        _swap_in(staging, live)

    monkeypatch.undo()

    # Live is back, with its ORIGINAL content.
    assert live.exists()
    ids = _read_back(live)
    assert len(ids) == 15
    assert all(i.startswith("old_") for i in ids)

    # The staged index was not consumed, so the failure is diagnosable.
    assert staging.exists()

    # And no orphan backup was left claiming to be a preserved index.
    orphans = [
        p for p in tmp_path.iterdir() if p.name.startswith("vector_db.backup-")
    ]
    assert orphans == [], f"rollback left a stray backup: {orphans}"

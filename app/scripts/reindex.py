"""
Deterministic re-index of the persisted vector store (Phase 6 sections 9-11).

WHY THIS EXISTS
---------------
The audit found the live index holding chunks written by three different
generations of the serializer: 7 chunks with 4 metadata keys, 9 with 28, and
none at the current schema. Everything downstream that depends on metadata --
identifier boosting, version-aware ranking, clause citation, the knowledge
join -- was therefore inert on real data while every unit test passed, because
the tests wrote fresh chunks and the application only ever asked
`collection.count() > 0`.

An index in that state cannot be repaired in place. It has to be rebuilt from
the authoritative inputs.

SOURCE OF TRUTH
---------------
The source PDFs under data/raw/ are the authoritative ingestion input, NOT
data/chunks/. The audit confirmed the chunk JSON is incomplete: it holds
sample_test_chunks.json only, so the Product-Manual document exists in the
vector store with no chunk file behind it. Rebuilding from data/chunks/ would
silently drop that document while reporting success.

SAFETY MODEL (section 10)
-------------------------
A rebuild never writes into the live directory. It builds a staging index,
validates it against the canonical schema, and only then swaps:

    build staging -> validate staging -> rename live to backup -> rename
    staging to live -> reopen from disk -> report

A failure at any point before the swap leaves the live index untouched. A
failure during the swap is rolled back. The displaced index is renamed, never
deleted -- this tool does not destroy an index, on the reasoning that an
operator can always delete a backup but cannot recover one that was never
kept.

USAGE
    python -m app.scripts.reindex --dry-run     # list what would be ingested
    python -m app.scripts.reindex --rebuild     # full rebuild with swap
    python -m app.scripts.reindex --probe        # inspect the live index only
"""

import argparse
import gc
import json
import logging
import os
import shutil
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import app  # noqa: F401  -- sets the TF-disabling env vars before transformers

from app.config import VECTOR_DB_DIR, ensure_directories, settings
from app.index_integrity import (
    check_index_integrity,
    log_integrity_report,
)
from app.index_schema import (
    CHUNK_INDEX_SCHEMA_VERSION,
    UNKNOWN_INT,
    UNKNOWN_STR,
)

logger = logging.getLogger(__name__)

#: Chunks read per page when probing. Chroma materializes whole pages, so a
#: large store is walked incrementally rather than loaded at once.
PROBE_PAGE_SIZE = 500

#: Suffix for the staging index built during a rebuild.
STAGING_SUFFIX = ".rebuild"

#: Prefix for a displaced live index. Timestamped, so successive rebuilds do
#: not overwrite each other's backups.
BACKUP_PREFIX = "backup-"

#: Rename attempts during a swap, and the pause between them.
#:
#: A directory rename on Windows fails with EACCES while any handle inside it
#: is open, and handle release is not synchronous with dropping the last Python
#: reference: Chroma's store lives behind a Rust binding whose files close when
#: the object is finalized, which can lag a `del` by a collection cycle. An
#: on-access virus scanner reading a freshly written index produces the same
#: transient failure. Both clear in well under a second, so a short bounded
#: retry converts a spurious hard failure into a brief wait -- while still
#: failing loudly if something holds the directory open for real.
SWAP_RENAME_ATTEMPTS = 8
SWAP_RENAME_DELAY_SECONDS = 0.5


# ============================================================
# PROBE -- read what is actually on disk
# ============================================================

@dataclass
class IndexProbe:
    """
    Field-level coverage of a persisted collection.

    Separate from IndexIntegrityReport, which samples and judges. This walks
    EVERY chunk and counts, because section 11 asks for distributions over
    the whole store and a sample cannot answer "how many chunks lack a page
    number".
    """

    collection_name: str = ""
    total_chunks: int = 0
    schema_versions: Dict[str, int] = field(default_factory=dict)
    key_counts: Dict[int, int] = field(default_factory=dict)
    relation_counts: Dict[str, int] = field(default_factory=dict)
    document_types: Dict[str, int] = field(default_factory=dict)
    #: field name -> number of chunks carrying a genuine (non-sentinel) value
    coverage: Dict[str, int] = field(default_factory=dict)
    documents: Dict[str, int] = field(default_factory=dict)
    context_methods: Dict[str, int] = field(default_factory=dict)

    def pct(self, field_name: str) -> float:
        if not self.total_chunks:
            return 0.0
        return 100.0 * self.coverage.get(field_name, 0) / self.total_chunks

    def to_dict(self) -> Dict[str, Any]:
        return {
            "collection": self.collection_name,
            "expected_schema_version": CHUNK_INDEX_SCHEMA_VERSION,
            "total_chunks": self.total_chunks,
            "schema_versions": self.schema_versions,
            "key_count_histogram": {str(k): v for k, v in self.key_counts.items()},
            "standard_relation": self.relation_counts,
            "document_types": self.document_types,
            "documents": self.documents,
            "context_generation_methods": self.context_methods,
            "coverage_counts": self.coverage,
            "coverage_pct": {
                name: round(self.pct(name), 1) for name in sorted(self.coverage)
            },
        }


#: Fields whose coverage section 11 asks about, plus the ones needed to tell
#: a citable chunk from an uncitable one.
_COVERAGE_STR_FIELDS = (
    "chunk_id",
    "document_id",
    "source_hash",
    "source_file",
    "standard_id",
    "version_id",
    "knowledge_clause_id",
    "clause_id",
    "standard_number",
    "standard_title",
    "edition_or_version",
    "amendment_number",
    "section",
    "heading_context",
    "contextualized_content",
)
_COVERAGE_INT_FIELDS = ("page_start", "page_end", "standard_year", "chunk_index")


def probe_collection(collection: Any) -> IndexProbe:
    """
    Walk every chunk in a persisted collection and count real coverage.

    Sentinels are decoded rather than counted as values: the index stores
    absence as "" for strings and -1 for integers because Chroma cannot hold
    null, so a naive `if value` would count "unknown page" as page 0 present
    and inflate every provenance number in the report.
    """
    probe = IndexProbe(collection_name=getattr(collection, "name", ""))
    probe.total_chunks = collection.count()

    versions: Counter = Counter()
    key_counts: Counter = Counter()
    relations: Counter = Counter()
    doc_types: Counter = Counter()
    documents: Counter = Counter()
    methods: Counter = Counter()
    coverage: Counter = Counter()

    offset = 0
    seen = 0
    while offset < probe.total_chunks:
        page = collection.get(
            limit=PROBE_PAGE_SIZE, offset=offset, include=["metadatas"]
        )
        metadatas = page.get("metadatas") or []
        if not metadatas:
            break

        for meta in metadatas:
            meta = meta or {}
            seen += 1
            versions[str(meta.get("schema_version", "<absent>"))] += 1
            key_counts[len(meta)] += 1
            relations[str(meta.get("standard_relation", "<absent>"))] += 1
            doc_types[str(meta.get("document_type") or "<unknown>")] += 1
            documents[str(meta.get("document_id") or "<unknown>")] += 1
            methods[str(meta.get("context_generation_method") or "<absent>")] += 1

            for name in _COVERAGE_STR_FIELDS:
                value = meta.get(name)
                if isinstance(value, str) and value != UNKNOWN_STR:
                    coverage[name] += 1
                elif value is not None and not isinstance(value, str):
                    coverage[name] += 1

            for name in _COVERAGE_INT_FIELDS:
                value = meta.get(name)
                if isinstance(value, int) and value != UNKNOWN_INT:
                    coverage[name] += 1

        offset += len(metadatas)

    if seen != probe.total_chunks:
        logger.warning(
            "Probe walked %d chunks but count() reported %d.", seen, probe.total_chunks
        )
        probe.total_chunks = seen

    probe.schema_versions = dict(versions)
    probe.key_counts = dict(sorted(key_counts.items()))
    probe.relation_counts = dict(relations)
    probe.document_types = dict(doc_types)
    probe.documents = dict(documents)
    probe.context_methods = dict(methods)
    probe.coverage = dict(coverage)
    return probe


def format_probe(probe: IndexProbe) -> str:
    """Human-readable probe output for an operator's terminal."""
    lines = [
        "",
        "=" * 62,
        f"PERSISTED INDEX PROBE  (collection: {probe.collection_name or '?'})",
        "=" * 62,
        f"total chunks            : {probe.total_chunks}",
        f"expected schema_version : {CHUNK_INDEX_SCHEMA_VERSION}",
        f"schema_version dist     : {probe.schema_versions}",
        f"metadata key histogram  : {probe.key_counts}",
        f"standard_relation dist  : {probe.relation_counts}",
        f"document_type dist      : {probe.document_types}",
        f"context method dist     : {probe.context_methods}",
        f"distinct documents      : {len(probe.documents)}",
        "",
        "coverage (chunks with a genuine value, sentinels excluded):",
    ]
    for name in _COVERAGE_STR_FIELDS + _COVERAGE_INT_FIELDS:
        count = probe.coverage.get(name, 0)
        lines.append(f"  {name:<24} {count:>6} / {probe.total_chunks}  ({probe.pct(name):5.1f}%)")

    stale = {
        version: count
        for version, count in probe.schema_versions.items()
        if version != CHUNK_INDEX_SCHEMA_VERSION
    }
    lines.append("")
    if stale:
        lines.append(f"STALE-SCHEMA CHUNKS PRESENT: {stale}")
    else:
        lines.append("stale-schema chunks: 0")
    lines.append("=" * 62)
    return "\n".join(lines)


# ============================================================
# REBUILD
# ============================================================

@dataclass
class RebuildResult:
    """Outcome of one rebuild attempt."""

    ok: bool = False
    pdfs_found: int = 0
    documents_ingested: int = 0
    documents_failed: int = 0
    chunks_indexed: int = 0
    failures: List[Dict[str, str]] = field(default_factory=list)
    backup_path: Optional[Path] = None
    swapped: bool = False
    probe: Optional[IndexProbe] = None
    integrity: Optional[Any] = None
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "pdfs_found": self.pdfs_found,
            "documents_ingested": self.documents_ingested,
            "documents_failed": self.documents_failed,
            "chunks_indexed": self.chunks_indexed,
            "failures": self.failures,
            "backup_path": str(self.backup_path) if self.backup_path else None,
            "swapped": self.swapped,
            "probe": self.probe.to_dict() if self.probe else None,
            "integrity": self.integrity.to_dict() if self.integrity else None,
            "message": self.message,
        }


def _open_client(path: Path):
    """
    Open a PersistentClient, first dropping Chroma's cached system for it.

    Chroma caches a system instance per path. After a directory swap the
    cached instance still points at the old inode's open SQLite handles, so
    a client obtained without clearing the cache can read the index that was
    just moved aside -- which would make the post-swap verification report
    on the wrong store.
    """
    import chromadb
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()
    return chromadb.PersistentClient(path=str(path))


def _discover_pdfs(source: Optional[Path]) -> List[Path]:
    """Authoritative ingestion inputs: the PDFs themselves."""
    from app.steps.pipeline import find_all_pdfs

    if source is None:
        return find_all_pdfs()

    source = Path(source)
    if source.is_file():
        return [source]
    if not source.is_dir():
        raise FileNotFoundError(f"Source path not found: {source}")
    return sorted(source.rglob("*.pdf"))


def _release_client(client: Any) -> None:
    """
    Close a Chroma client's underlying store and drop its cached system.

    `clear_system_cache()` alone is not a close: its whole body assigns a new
    empty dict to the identifier map. The SQLite connection pool is closed by
    `System.stop()`, and until that runs the files under the store's directory
    stay open -- which on Windows makes the directory unrenameable.

    Dropping the last Python reference is also not enough on its own. The
    collection is handed to `process_pdf`, which passes it further down the
    pipeline, so a reference can outlive the local name; and the store sits
    behind a Rust binding that releases its files on finalization. An explicit
    stop plus a forced collection cycle makes the release deterministic instead
    of leaving it to whenever the interpreter next collects.
    """
    from chromadb.api.client import SharedSystemClient

    system = getattr(client, "_system", None)
    if system is not None:
        try:
            system.stop()
        except Exception as exc:  # a failed stop must not mask the rebuild
            logger.warning("Chroma system did not stop cleanly: %s", exc)

    SharedSystemClient.clear_system_cache()
    gc.collect()


def _rename_with_retry(src: Path, dst: Path, what: str) -> None:
    """
    Rename `src` to `dst`, retrying briefly on a transient access error.

    Retries only OSError, and only for a bounded number of short attempts. A
    directory genuinely held open by another process still fails -- which is
    the correct outcome, because silently falling back to a copy would give up
    the atomicity that makes the swap safe.
    """
    last: Optional[OSError] = None
    for attempt in range(1, SWAP_RENAME_ATTEMPTS + 1):
        try:
            os.rename(src, dst)
            if attempt > 1:
                logger.info("%s succeeded on attempt %d.", what, attempt)
            return
        except OSError as exc:
            last = exc
            if attempt == SWAP_RENAME_ATTEMPTS:
                break
            logger.warning(
                "%s failed (attempt %d/%d): %s -- retrying in %.1fs",
                what,
                attempt,
                SWAP_RENAME_ATTEMPTS,
                exc,
                SWAP_RENAME_DELAY_SECONDS,
            )
            gc.collect()
            time.sleep(SWAP_RENAME_DELAY_SECONDS)

    assert last is not None
    raise last


def _swap_in(staging: Path, live: Path) -> Path:
    """
    Replace `live` with `staging`, keeping the displaced index as a backup.

    Two renames rather than a copy: a rename is atomic within a filesystem,
    so there is no window in which the live path holds a half-written store.
    If the second rename fails the first is undone, because a live path left
    missing would be a worse outcome than a failed rebuild.
    """
    backup = live.parent / f"{live.name}.{BACKUP_PREFIX}{time.strftime('%Y%m%d-%H%M%S')}"

    if live.exists():
        _rename_with_retry(live, backup, "Displacing live index")
        logger.info("Displaced index preserved at %s", backup)
    else:
        backup = None  # nothing to preserve

    try:
        _rename_with_retry(staging, live, "Promoting staged index")
    except OSError:
        if backup is not None and backup.exists():
            _rename_with_retry(backup, live, "Restoring original index")
            logger.error("Swap failed; original index restored to %s", live)
        raise

    return backup


def rebuild_index(
    source: Optional[Path] = None,
    live_dir: Path = VECTOR_DB_DIR,
    collection_name: Optional[str] = None,
    stop_on_error: bool = False,
) -> RebuildResult:
    """
    Rebuild the persisted index from source PDFs and swap it into place.

    Every stage of the current pipeline runs: extraction, cleaning,
    structuring, normalization, chunking with provenance, knowledge
    population, contextualization, embedding, and validated index writes.
    Nothing is reused from a previous run's intermediate files, because a
    stale intermediate is exactly what produced the mixed-schema index this
    command exists to fix.
    """
    from app.steps.embed import load_embedding_model
    from app.steps.pipeline import process_pdf

    result = RebuildResult()
    collection_name = collection_name or settings.CHROMA_COLLECTION_NAME
    live_dir = Path(live_dir)
    staging = live_dir.parent / f"{live_dir.name}{STAGING_SUFFIX}"

    ensure_directories()

    pdfs = _discover_pdfs(source)
    result.pdfs_found = len(pdfs)
    if not pdfs:
        result.message = (
            "No source PDFs found. Refusing to build an empty index over a "
            "working one."
        )
        return result

    logger.info("Discovered %d source PDF(s).", len(pdfs))

    # -- staging ---------------------------------------------------------
    #
    # Removed rather than reused: a staging directory left behind by an
    # interrupted run would contribute its chunks to this build and the
    # totals would not correspond to the PDFs actually ingested. It is our
    # own scratch path, never the live index.
    if staging.exists():
        logger.info("Clearing previous staging index at %s", staging)
        shutil.rmtree(staging)

    client = _open_client(staging)
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"description": "BIS document embeddings"},
    )

    embedder = load_embedding_model()

    for index, pdf in enumerate(pdfs, start=1):
        logger.info("[%d/%d] Ingesting %s", index, len(pdfs), pdf.name)
        try:
            outcome = process_pdf(
                pdf, embedding_model=embedder, collection=collection
            )
            result.documents_ingested += 1
            result.chunks_indexed += int(outcome.get("chunks_indexed") or 0)
        except Exception as exc:  # one bad PDF must not abort the rebuild
            logger.error("Failed to ingest %s: %s", pdf.name, exc)
            result.documents_failed += 1
            result.failures.append({"pdf": str(pdf), "error": str(exc)})
            if stop_on_error:
                result.message = f"Aborted at {pdf.name}: {exc}"
                return result

    if result.chunks_indexed == 0:
        result.message = (
            f"Rebuild produced 0 chunks from {len(pdfs)} PDF(s). The live "
            f"index was left untouched."
        )
        return result

    # -- validate BEFORE swapping ---------------------------------------
    staged_report = check_index_integrity(collection)
    if not staged_report.is_healthy:
        result.integrity = staged_report
        result.message = (
            f"Staged index failed validation ({staged_report.state}): "
            f"{staged_report.message} The live index was left untouched; the "
            f"staging index remains at {staging} for inspection."
        )
        return result

    # -- swap ------------------------------------------------------------
    #
    # The client must be released first. On Windows an open SQLite handle
    # prevents renaming the directory that holds it, so a rebuild would fail
    # at the last step with the new index already fully built. This is not
    # hypothetical: it is exactly how the first real rebuild failed, with a
    # validated 1.6 MB staged index and an EACCES on the promoting rename.
    del collection
    _release_client(client)
    del client

    result.backup_path = _swap_in(staging, live_dir)
    result.swapped = True

    # -- verify what is now on disk, read fresh --------------------------
    #
    # Reopened from the live path rather than reusing the staging handle:
    # the claim being made is about the store an application will load at
    # startup, and only a fresh read of that path can support it.
    verify_client = _open_client(live_dir)
    verify_collection = verify_client.get_collection(name=collection_name)

    result.probe = probe_collection(verify_collection)
    result.integrity = check_index_integrity(verify_collection)
    result.ok = result.integrity.is_healthy
    result.message = (
        f"Rebuilt {result.documents_ingested} document(s), "
        f"{result.chunks_indexed} chunks. Live index state: "
        f"{result.integrity.state}."
    )
    return result


# ============================================================
# CLI
# ============================================================

def _probe_live(live_dir: Path, collection_name: str) -> int:
    """Inspect the live index without modifying anything."""
    if not Path(live_dir).exists():
        print(f"No index at {live_dir}")
        return 1

    client = _open_client(Path(live_dir))
    try:
        collection = client.get_collection(name=collection_name)
    except Exception as exc:
        print(f"Could not open collection {collection_name!r} at {live_dir}: {exc}")
        return 1

    probe = probe_collection(collection)
    print(format_probe(probe))

    report = check_index_integrity(collection)
    log_integrity_report(report)
    print(f"\nintegrity state: {report.state}  healthy={report.is_healthy}")
    if report.message:
        print(f"  {report.message}")
    if report.remediation:
        print(f"  remediation: {report.remediation}")
    return 0 if report.is_healthy else 2


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.scripts.reindex",
        description="Rebuild or inspect the persisted BIS vector index.",
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Rebuild from source PDFs and swap the new index into place.",
    )
    parser.add_argument(
        "--probe", action="store_true",
        help="Inspect the live index and exit. Never writes.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List the PDFs a rebuild would ingest, then exit.",
    )
    parser.add_argument(
        "--source", type=Path, default=None,
        help="PDF file or directory to ingest. Defaults to data/raw/ recursively.",
    )
    parser.add_argument(
        "--live-dir", type=Path, default=VECTOR_DB_DIR,
        help=f"Persisted index directory. Default: {VECTOR_DB_DIR}",
    )
    parser.add_argument(
        "--collection", default=settings.CHROMA_COLLECTION_NAME,
        help=f"Collection name. Default: {settings.CHROMA_COLLECTION_NAME}",
    )
    parser.add_argument(
        "--stop-on-error", action="store_true",
        help="Abort the rebuild at the first PDF that fails.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit the result as JSON on stdout.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.probe:
        return _probe_live(args.live_dir, args.collection)

    if args.dry_run:
        pdfs = _discover_pdfs(args.source)
        print(f"{len(pdfs)} source PDF(s) would be ingested:")
        for pdf in pdfs:
            print(f"  {pdf}  ({pdf.stat().st_size:,} bytes)")
        print(f"\nLive index      : {args.live_dir}")
        print(f"Staging index   : {args.live_dir}{STAGING_SUFFIX}")
        print(f"Collection      : {args.collection}")
        print(f"Target schema   : {CHUNK_INDEX_SCHEMA_VERSION}")
        print("\nNothing was modified.")
        return 0

    if not args.rebuild:
        parser.print_help()
        print("\nNothing was modified. Pass --rebuild, --probe, or --dry-run.")
        return 0

    result = rebuild_index(
        source=args.source,
        live_dir=args.live_dir,
        collection_name=args.collection,
        stop_on_error=args.stop_on_error,
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        if result.probe:
            print(format_probe(result.probe))
        print("")
        print("=" * 62)
        print("REBUILD REPORT")
        print("=" * 62)
        print(f"pdfs found          : {result.pdfs_found}")
        print(f"documents ingested  : {result.documents_ingested}")
        print(f"documents failed    : {result.documents_failed}")
        print(f"chunks indexed      : {result.chunks_indexed}")
        print(f"index swapped       : {result.swapped}")
        print(f"previous index kept : {result.backup_path}")
        if result.integrity:
            print(f"integrity state     : {result.integrity.state}")
        print(f"result              : {'OK' if result.ok else 'NOT OK'}")
        print(f"message             : {result.message}")
        for failure in result.failures:
            print(f"  FAILED {failure['pdf']}: {failure['error']}")
        print("=" * 62)

    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())

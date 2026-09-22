"""app/acquisition/sync/__main__.py

CLI Entrypoint for BIS Knowledge Acquisition Engine.

Usage:
    python -m app.acquisition.sync --source bis --mode discovery [--limit 20] [--terms 1293,16444,3055]
    python -m app.acquisition.sync --source bis --mode download [--limit 10]
    python -m app.acquisition.sync --source bis --mode ingest [--limit 10]
    python -m app.acquisition.sync --source bis --mode all [--limit 10] [--terms 1293,16444,3055]
"""

import argparse
import json
import logging
import sys
from typing import List, Optional

from app.acquisition.sync.coordinator import default_coordinator

# Configure logging with ASCII-safe formatter for Windows console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("bis_acquisition_cli")


def safe_print(msg: str) -> None:
    """Print message to stdout ensuring Windows console compatibility."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BIS Official Knowledge Acquisition & Pipeline CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--source",
        type=str,
        default="bis",
        help="Source identifier to acquire from (e.g. 'bis')",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["discovery", "download", "ingest", "all"],
        default="all",
        help="Acquisition phase to execute",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of items to discover/download/ingest in this batch",
    )
    parser.add_argument(
        "--terms",
        type=str,
        default="1293,16444,1786,3055,9873,694,13422,9283",
        help="Comma-separated list of standard numbers or keywords to target",
    )

    args = parser.parse_args()
    filter_terms: Optional[List[str]] = [t.strip() for t in args.terms.split(",") if t.strip()] if args.terms else None

    safe_print("=" * 70)
    safe_print("  BIS KNOWLEDGE ACQUISITION ENGINE - OFFICIAL PIPELINE")
    safe_print(f"  Source: {args.source} | Mode: {args.mode.upper()} | Limit: {args.limit}")
    if filter_terms:
        safe_print(f"  Target Terms: {', '.join(filter_terms)}")
    safe_print("=" * 70)

    coordinator = default_coordinator

    try:
        if args.mode == "discovery":
            safe_print("\n>>> PHASE 1: DISCOVERY (Scanning official BIS endpoints)...")
            records = coordinator.run_discovery(filter_terms=filter_terms, limit=args.limit)
            safe_print(f"\n[OK] Discovery completed: {len(records)} records discovered and cataloged.")
            for i, r in enumerate(records[:10], 1):
                safe_print(f"  {i}. [{r.document_type}] {r.standard_number} - {r.title[:60]} ({r.rights_status})")
            if len(records) > 10:
                safe_print(f"  ... and {len(records) - 10} more records in SQLite catalog.")

        elif args.mode == "download":
            safe_print("\n>>> PHASE 2: DOWNLOAD (Fetching public official documents)...")
            results = coordinator.run_download(limit=args.limit)
            success_count = sum(1 for r in results if r.success)
            fail_count = len(results) - success_count
            safe_print(f"\n[OK] Download completed: {success_count} succeeded, {fail_count} failed.")
            for r in results:
                status_icon = "[OK]" if r.success else "[ERR]"
                safe_print(f"  {status_icon} {r.url} -> {r.local_path} ({r.file_size} bytes, hash: {r.content_hash[:12] if r.content_hash else 'N/A'})")

        elif args.mode == "ingest":
            safe_print("\n>>> PHASE 3: INGESTION & INDEXING (Chunking + BGE Embedding + ChromaDB)...")
            results = coordinator.run_ingestion(limit=args.limit)
            safe_print(f"\n[OK] Ingestion completed: {len(results)} documents processed.")
            total_chunks = sum(r.get("chunks_indexed", 0) for r in results)
            for r in results:
                safe_print(f"  * {r.get('standard_number')}: {r.get('chunks_indexed')} chunks indexed ({r.get('local_path')})")
            safe_print(f"\nTotal new chunks indexed into ChromaDB: {total_chunks}")

        elif args.mode == "all":
            safe_print("\n>>> FULL CYCLE: DISCOVERY -> DOWNLOAD -> INGESTION -> VECTOR INDEXING...")
            summary = coordinator.run_all(
                filter_terms=filter_terms,
                download_limit=args.limit,
                ingest_limit=args.limit,
            )
            safe_print("\n" + "=" * 70)
            safe_print("  ACQUISITION BATCH EXECUTION SUMMARY")
            safe_print("=" * 70)
            safe_print(f"  Elapsed Time:     {summary.get('elapsed_seconds')}s")
            safe_print(f"  Discovered:       {summary.get('total_discovered')}")
            safe_print(f"  Downloaded:       {summary.get('total_downloaded')}")
            safe_print(f"  Ingested/Indexed: {summary.get('total_ingested')}")
            safe_print("-" * 70)
            for item in summary.get("ingested_details", []):
                safe_print(f"  Indexed: {item.get('standard_number')} -> {item.get('chunks_indexed')} chunks")
            safe_print("=" * 70)

        return 0

    except Exception as exc:
        logger.exception("Acquisition run failed: %s", exc)
        safe_print(f"\n[FATAL ERROR] Acquisition execution failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

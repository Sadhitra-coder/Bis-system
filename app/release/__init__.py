"""app/release package.

Corpus Release & Azure Synchronization subsystem.
Manages immutable releases of the BIS knowledge base, manifests,
and automated publishing to Azure Container Apps.
"""

from app.release.manifest import (
    CorpusManifest,
    create_corpus_release,
    get_latest_release,
    list_releases,
    load_release_manifest,
    verify_release_integrity,
)

__all__ = [
    "CorpusManifest",
    "create_corpus_release",
    "get_latest_release",
    "list_releases",
    "load_release_manifest",
    "verify_release_integrity",
]

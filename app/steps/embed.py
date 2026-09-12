import json
import logging
from pathlib import Path

from typing import Any, Dict, List, Optional

import chromadb
from sentence_transformers import SentenceTransformer

from app.config import (
    CHUNKS_DATA_DIR,
    VECTOR_DB_DIR,
    settings,
)
from app.index_schema import (
    CHUNK_INDEX_SCHEMA_VERSION,
    build_chunk_index_metadata,
    validate_chunk_index_metadata,
)


# =========================================================
# CONFIGURATION
#
# Directory layout comes from app.config module constants;
# tunable runtime values come from app.config.settings.
# =========================================================

CHUNKS_DIR = CHUNKS_DATA_DIR
CHROMA_DIR = VECTOR_DB_DIR
COLLECTION_NAME = settings.CHROMA_COLLECTION_NAME
EMBEDDING_MODEL = settings.EMBEDDING_MODEL
BATCH_SIZE = settings.EMBEDDING_BATCH_SIZE


# =========================================================
# LOGGING
# =========================================================

logger = logging.getLogger(__name__)



# =========================================================
# LOAD EMBEDDING MODEL
# =========================================================

def load_embedding_model():
    logger.info(
        f"Loading embedding model: {EMBEDDING_MODEL}"
    )

    model = SentenceTransformer(
        EMBEDDING_MODEL
    )

    logger.info("Embedding model loaded successfully.")

    return model


# =========================================================
# INITIALIZE CHROMADB
# =========================================================

def get_collection():

    logger.info(
        f"Initializing ChromaDB at: {CHROMA_DIR}"
    )

    client = chromadb.PersistentClient(
        path=str(CHROMA_DIR)
    )

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={
            "description":
                "BIS document embeddings"
        }
    )

    logger.info(
        f"Collection ready: {COLLECTION_NAME}"
    )

    return collection


# =========================================================
# LOAD CHUNKS FROM JSON FILE
# =========================================================

def load_chunks(json_file):

    logger.info(
        f"Reading chunks: {json_file.name}"
    )

    with open(
        json_file,
        "r",
        encoding="utf-8"
    ) as file:

        data = json.load(file)

    return data.get(
        "chunks",
        []
    )


# =========================================================
# PREPARE DATA
# =========================================================

def prepare_chunks(chunks):
    """
    Serialize chunks into (ids, documents, metadatas) for the vector index.

    Metadata assembly is delegated entirely to app.index_schema, which owns
    the single canonical definition of the persisted contract. This function
    is deliberately thin: it decides only what TEXT gets embedded, because
    that is a retrieval decision, not a schema decision.

    Before Phase 6 the ~30 metadata fields were written out by hand here,
    which made the contract invisible to validation and versioning, and
    encoded unknown pages and years as 0 — indistinguishable from real
    values of 0.
    """
    ids = []
    documents = []
    metadatas = []

    use_contextual = bool(getattr(settings, "ENABLE_CONTEXTUAL_RETRIEVAL", True))

    for chunk in chunks:

        chunk_id = chunk.get("chunk_id")
        content = chunk.get("content", "")

        if not content.strip():
            logger.warning(f"Skipping empty chunk: {chunk_id}")
            continue

        # Phase 5 Contextual Retrieval.
        # source_content is ALWAYS the unaltered chunk text. Only the
        # retrieval representation may carry a generated context prefix.
        if use_contextual:
            from app.rag.contextualizer import contextualize_chunk
            ctx = contextualize_chunk(chunk)
            source_content = content
            contextualized_content = ctx.contextualized_content
            context_method = ctx.context_generation_method
            context_version = ctx.context_generation_version
        else:
            source_content = content
            contextualized_content = content
            context_method = "none"
            context_version = "none"

        metadata = build_chunk_index_metadata(
            chunk,
            source_content=source_content,
            contextualized_content=contextualized_content,
            context_generation_method=context_method,
            context_generation_version=context_version,
        )

        ids.append(chunk_id)
        documents.append(contextualized_content if use_contextual else content)
        metadatas.append(metadata)

    return (
        ids,
        documents,
        metadatas
    )


# =========================================================
# WRITE-PATH INTEGRITY GATE
# =========================================================

class IndexWriteRejected(ValueError):
    """Raised when a batch cannot legally be written to the vector index."""


def validate_write_batch(ids, documents, metadatas, *, strict: bool = True):
    """
    Validate a batch against the canonical index schema before it is written.

    Every chunk entering the index passes through here. A malformed chunk is
    surfaced loudly rather than silently persisted: a bad row in a vector
    store is invisible afterwards — retrieval simply returns slightly wrong
    provenance forever, and no test that inspects in-memory objects can see
    it.

    In strict mode the whole batch is rejected. Otherwise offending chunks
    are dropped and the surviving batch is returned, so a bulk re-index can
    make progress while still refusing to write garbage.

    Returns (ids, documents, metadatas, rejections).
    """
    rejections = []

    if not (len(ids) == len(documents) == len(metadatas)):
        raise IndexWriteRejected(
            f"batch arity mismatch: {len(ids)} ids, {len(documents)} documents, "
            f"{len(metadatas)} metadatas"
        )

    keep_ids, keep_docs, keep_metas = [], [], []

    for chunk_id, document, metadata in zip(ids, documents, metadatas):
        problems = validate_chunk_index_metadata(metadata)

        if not str(chunk_id or "").strip():
            problems.append("chroma id is empty")
        elif metadata.get("chunk_id") != str(chunk_id):
            problems.append(
                f"chroma id {chunk_id!r} != metadata chunk_id {metadata.get('chunk_id')!r}"
            )
        if not str(document or "").strip():
            problems.append("embedded document text is empty")

        if problems:
            rejections.append({"chunk_id": chunk_id, "problems": problems})
            continue

        keep_ids.append(chunk_id)
        keep_docs.append(document)
        keep_metas.append(metadata)

    if rejections:
        for rejection in rejections:
            logger.error(
                "Rejected chunk %s from index write: %s",
                rejection["chunk_id"], "; ".join(rejection["problems"]),
            )
        if strict:
            raise IndexWriteRejected(
                f"{len(rejections)} of {len(ids)} chunks violate index schema "
                f"{CHUNK_INDEX_SCHEMA_VERSION}; refusing to write. "
                f"First failure: {rejections[0]}"
            )

    return keep_ids, keep_docs, keep_metas, rejections


# =========================================================
# EMBED AND STORE
# =========================================================

def embed_chunks(
    model,
    collection,
    ids,
    documents,
    metadatas,
    strict_validation: bool = True,
):

    # Integrity gate: nothing reaches Chroma without conforming to the
    # canonical schema.
    ids, documents, metadatas, rejections = validate_write_batch(
        ids, documents, metadatas, strict=strict_validation
    )
    if rejections:
        logger.warning(
            "Proceeding with %d chunks after dropping %d schema-invalid chunks.",
            len(ids), len(rejections),
        )

    total_chunks = len(
        documents
    )

    logger.info(
        f"Total chunks to embed: {total_chunks}"
    )

    for start in range(
        0,
        total_chunks,
        BATCH_SIZE
    ):

        end = min(
            start + BATCH_SIZE,
            total_chunks
        )

        batch_ids = ids[start:end]

        batch_documents = (
            documents[start:end]
        )

        batch_metadatas = (
            metadatas[start:end]
        )

        logger.info(
            f"Embedding chunks "
            f"{start + 1}-{end}"
        )

        embeddings = model.encode(
            batch_documents,
            batch_size=BATCH_SIZE,
            normalize_embeddings=True,
            show_progress_bar=False
        )

        collection.upsert(

            ids=batch_ids,

            documents=batch_documents,

            embeddings=embeddings.tolist(),

            metadatas=batch_metadatas
        )

    logger.info(
        "Embedding and storage completed."
    )


# =========================================================
# PROCESS SINGLE CHUNK FILE
# =========================================================

def embed_chunk_file(
    chunks_path: Path,
    model: Optional[SentenceTransformer] = None,
    collection: Optional[Any] = None
) -> int:
    """
    Embed chunks from a single JSON file and index them into ChromaDB.

    Parameters
    ----------
    chunks_path:
        Path to the chunk JSON file.
    model:
        Preloaded SentenceTransformer model (optional).
    collection:
        Preloaded Chroma collection (optional).

    Returns
    -------
    int:
        Number of chunks embedded.
    """
    chunks_path = Path(chunks_path)
    if not chunks_path.exists():
        raise FileNotFoundError(f"Chunks file not found: {chunks_path}")

    chunks = load_chunks(chunks_path)
    ids, documents, metadatas = prepare_chunks(chunks)

    if not documents:
        logger.warning(f"No valid chunks to embed in {chunks_path.name}")
        return 0

    if model is None:
        model = load_embedding_model()
    if collection is None:
        collection = get_collection()

    embed_chunks(
        model=model,
        collection=collection,
        ids=ids,
        documents=documents,
        metadatas=metadatas
    )

    logger.info(
        "Embedded and indexed %d chunks from %s",
        len(documents),
        chunks_path.name
    )
    return len(documents)


# =========================================================
# PROCESS ALL CHUNK FILES
# =========================================================

def process_all_files():

    if not CHUNKS_DIR.exists():

        raise FileNotFoundError(
            f"Chunks directory not found: "
            f"{CHUNKS_DIR}"
        )

    json_files = list(
        CHUNKS_DIR.glob(
            "*.json"
        )
    )

    if not json_files:

        logger.warning(
            "No chunk JSON files found."
        )

        return

    logger.info(
        f"Found {len(json_files)} "
        f"chunk JSON file(s)"
    )

    model = load_embedding_model()

    collection = get_collection()

    total_processed = 0

    for json_file in json_files:

        logger.info(
            "-" * 50
        )

        logger.info(
            f"Processing: "
            f"{json_file.name}"
        )

        chunks = load_chunks(
            json_file
        )

        (
            ids,
            documents,
            metadatas
        ) = prepare_chunks(
            chunks
        )

        if not documents:

            logger.warning(
                "No valid chunks found."
            )

            continue

        embed_chunks(

            model=model,

            collection=collection,

            ids=ids,

            documents=documents,

            metadatas=metadatas
        )

        total_processed += len(
            documents
        )

    logger.info(
        "-" * 50
    )

    logger.info(
        f"Embedding finished."
    )

    logger.info(
        f"Chunks processed: "
        f"{total_processed}"
    )

    logger.info(
        f"Total chunks in database: "
        f"{collection.count()}"
    )


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    print(
        "\nStarting document embedding...\n"
    )

    process_all_files()

    print(
        "\nDocument embedding finished."
    )
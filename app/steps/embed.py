import json
import logging
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer


# =========================================================
# CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parents[2]

CHUNKS_DIR = BASE_DIR / "data" / "chunks"

CHROMA_DIR = BASE_DIR / "data" / "vector_db"

COLLECTION_NAME = "bis_documents"

EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"

BATCH_SIZE = 16


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s"
)

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

    ids = []
    documents = []
    metadatas = []

    for chunk in chunks:

        chunk_id = chunk.get(
            "chunk_id"
        )

        content = chunk.get(
            "content",
            ""
        )

        if not content.strip():

            logger.warning(
                f"Skipping empty chunk: {chunk_id}"
            )

            continue

        metadata = {

            "document_id":
                str(
                    chunk.get(
                        "document_id",
                        ""
                    )
                ),

            "source_file":
                str(
                    chunk.get(
                        "source_file",
                        ""
                    )
                ),

            "section":
                str(
                    chunk.get(
                        "section",
                        ""
                    )
                ),

            "heading_context":
                " > ".join(
                    chunk.get(
                        "heading_context",
                        []
                    )
                )
        }

        ids.append(
            chunk_id
        )

        documents.append(
            content
        )

        metadatas.append(
            metadata
        )

    return (
        ids,
        documents,
        metadatas
    )


# =========================================================
# EMBED AND STORE
# =========================================================

def embed_chunks(
    model,
    collection,
    ids,
    documents,
    metadatas
):

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
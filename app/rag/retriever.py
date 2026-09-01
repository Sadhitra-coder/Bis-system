import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

DEFAULT_COLLECTION_NAME = "bis_documents"

DEFAULT_EMBEDDING_MODEL = (
    "BAAI/bge-large-en-v1.5"
)


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def tokenize(text: str) -> List[str]:
    """
    Generic tokenizer for BM25.

    Handles:
    - normal words
    - numbers
    - standard numbers such as IS 3055
    - terms containing dots/hyphens
    """

    if not text:
        return []

    return re.findall(
        r"\b[\w.-]+\b",
        str(text).lower()
    )


def normalize_scores(
    scores: List[float]
) -> np.ndarray:
    """
    Min-max normalize scores to [0, 1].

    Handles empty and equal-score cases safely.
    """

    if not scores:
        return np.array(
            [],
            dtype=float
        )

    scores_array = np.asarray(
        scores,
        dtype=float
    )

    minimum = scores_array.min()
    maximum = scores_array.max()

    # All scores are identical
    if np.isclose(
        minimum,
        maximum
    ):
        # If all retrieval scores are zero,
        # there is no useful ranking signal.
        if np.isclose(
            maximum,
            0.0
        ):
            return np.zeros(
                len(scores_array),
                dtype=float
            )

        return np.ones(
            len(scores_array),
            dtype=float
        )

    return (
        scores_array - minimum
    ) / (
        maximum - minimum
    )


def find_project_root(
    start_path: Path
) -> Path:
    """
    Attempts to locate the project root dynamically.

    Looks upward for common project markers.

    Falls back to a reasonable parent directory.
    """

    markers = [
        "app",
        "data"
    ]

    current = start_path.resolve()

    if current.is_file():
        current = current.parent

    for directory in [
        current,
        *current.parents
    ]:

        marker_count = sum(
            (
                directory / marker
            ).exists()
            for marker in markers
        )

        if marker_count >= 2:

            return directory

    # Fallback
    return start_path.resolve().parents[2]


# ============================================================
# GENERIC JSON CHUNK EXTRACTION
# ============================================================

def extract_chunks_from_data(
    data: Any
) -> List[Dict[str, Any]]:
    """
    Extract chunk dictionaries from multiple possible
    JSON structures.

    Supported examples:

    1.
    {
        "chunks": [...]
    }

    2.
    [
        {...},
        {...}
    ]

    3.
    {
        "documents": [
            {
                "chunks": [...]
            }
        ]
    }

    4.
    Nested structures where a list of chunks appears
    under common keys.
    """

    chunks: List[
        Dict[str, Any]
    ] = []

    # --------------------------------------------------------
    # CASE 1: LIST
    # --------------------------------------------------------

    if isinstance(
        data,
        list
    ):

        for item in data:

            if isinstance(
                item,
                dict
            ):

                # Direct chunk
                if (
                    "content" in item
                    or "text" in item
                    or "page_content" in item
                ):

                    chunks.append(
                        item
                    )

                # Nested object
                else:

                    chunks.extend(
                        extract_chunks_from_data(
                            item
                        )
                    )

        return chunks

    # --------------------------------------------------------
    # CASE 2: DICTIONARY
    # --------------------------------------------------------

    if isinstance(
        data,
        dict
    ):

        # If this object itself looks like a chunk
        if (
            "content" in data
            or "text" in data
            or "page_content" in data
        ):

            chunks.append(
                data
            )

            return chunks

        # Common chunk container keys
        possible_keys = [

            "chunks",

            "documents",

            "data",

            "items",

            "records",

            "sections"

        ]

        for key in possible_keys:

            value = data.get(
                key
            )

            if isinstance(
                value,
                (
                    list,
                    dict
                )
            ):

                chunks.extend(
                    extract_chunks_from_data(
                        value
                    )
                )

        return chunks

    return chunks


def get_chunk_content(
    chunk: Dict[str, Any]
) -> str:
    """
    Extract text from a chunk using common field names.
    """

    possible_content_keys = [

        "content",

        "text",

        "page_content",

        "document",

        "chunk_text"

    ]

    for key in possible_content_keys:

        value = chunk.get(
            key
        )

        if value is not None:

            text = str(
                value
            ).strip()

            if text:

                return text

    return ""


def get_chunk_id(
    chunk: Dict[str, Any],
    fallback: str
) -> str:
    """
    Extract chunk ID from common field names.
    """

    possible_id_keys = [

        "chunk_id",

        "id",

        "document_id",

        "uuid"

    ]

    for key in possible_id_keys:

        value = chunk.get(
            key
        )

        if value:

            return str(
                value
            )

    return fallback


def extract_metadata(
    chunk: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Extract metadata generically.

    Preserves existing metadata while also collecting
    useful top-level fields.
    """

    metadata: Dict[
        str,
        Any
    ] = {}

    # Existing metadata
    existing_metadata = (
        chunk.get(
            "metadata",
            {}
        )
    )

    if isinstance(
        existing_metadata,
        dict
    ):

        metadata.update(
            existing_metadata
        )

    # Preserve useful top-level fields
    excluded_keys = [

        "chunk_id",

        "id",

        "content",

        "text",

        "page_content",

        "document",

        "chunk_text",

        "metadata",

        "embedding"
    ]

    for key, value in chunk.items():

        if key in excluded_keys:

            continue

        # Avoid putting complex structures
        # into metadata unnecessarily.
        if isinstance(
            value,
            (
                str,
                int,
                float,
                bool
            )
        ) or value is None:

            metadata.setdefault(
                key,
                value
            )

    return metadata


# ============================================================
# HYBRID RETRIEVER
# ============================================================

class HybridRetriever:

    def __init__(
        self,
        persist_directory: Optional[
            str
        ] = None,

        chunks_directory: Optional[
            str
        ] = None,

        collection_name: str = (
            DEFAULT_COLLECTION_NAME
        ),

        embedding_model: str = (
            DEFAULT_EMBEDDING_MODEL
        ),

        alpha: float = 0.65
    ):
        """
        Generic Hybrid Retriever.

        Pipeline:

        Query
          │
          ├── Dense Retrieval
          │       SentenceTransformer
          │       → ChromaDB
          │
          ├── Keyword Retrieval
          │       BM25
          │
          └── Score Fusion
                  │
                  ▼
            Hybrid Results


        alpha:

        1.0 = Dense only
        0.0 = BM25 only
        """

        if not 0.0 <= alpha <= 1.0:

            raise ValueError(
                "alpha must be between "
                "0.0 and 1.0"
            )

        print("\n" + "=" * 70)
        print(
            "INITIALIZING HYBRID RETRIEVER"
        )
        print("=" * 70 + "\n")

        self.alpha = alpha

        self.collection_name = (
            collection_name
        )

        self.embedding_model = (
            embedding_model
        )

        # ----------------------------------------------------
        # PROJECT ROOT
        # ----------------------------------------------------

        current_file = Path(
            __file__
        ).resolve()

        self.project_root = (
            find_project_root(
                current_file
            )
        )

        logger.info(
            f"Project root: "
            f"{self.project_root}"
        )

        # ----------------------------------------------------
        # VECTOR DATABASE PATH
        # ----------------------------------------------------

        if persist_directory:

            self.persist_directory = (
                Path(
                    persist_directory
                ).resolve()
            )

        else:

            self.persist_directory = (
                self.project_root
                / "data"
                / "vector_db"
            )

        logger.info(
            f"ChromaDB path: "
            f"{self.persist_directory}"
        )

        # ----------------------------------------------------
        # CHUNKS PATH
        # ----------------------------------------------------

        if chunks_directory:

            self.chunks_dir = (
                Path(
                    chunks_directory
                ).resolve()
            )

        else:

            self.chunks_dir = (
                self.project_root
                / "data"
                / "chunks"
            )

        logger.info(
            f"Chunks directory: "
            f"{self.chunks_dir}"
        )

        # ----------------------------------------------------
        # VALIDATE PATHS
        # ----------------------------------------------------

        if not (
            self.persist_directory.exists()
        ):

            raise FileNotFoundError(
                "Vector database directory "
                "not found:\n"
                f"{self.persist_directory}"
            )

        if not (
            self.chunks_dir.exists()
        ):

            raise FileNotFoundError(
                "Chunks directory "
                "not found:\n"
                f"{self.chunks_dir}"
            )

        # ----------------------------------------------------
        # EMBEDDING MODEL
        # ----------------------------------------------------

        logger.info(
            f"Loading embedding model: "
            f"{embedding_model}"
        )

        self.embedder = (
            SentenceTransformer(
                embedding_model
            )
        )

        logger.info(
            "Embedding model loaded."
        )

        # ----------------------------------------------------
        # CHROMADB
        # ----------------------------------------------------

        logger.info(
            "Connecting to ChromaDB..."
        )

        self.chroma_client = (
            chromadb.PersistentClient(
                path=str(
                    self.persist_directory
                )
            )
        )

        available_collections = (
            self.chroma_client
            .list_collections()
        )

        if not available_collections:

            raise RuntimeError(
                "No ChromaDB collections found."
            )

        collection_names = [

            collection.name

            for collection
            in available_collections

        ]

        logger.info(
            "Available collections: "
            f"{collection_names}"
        )

        if (
            self.collection_name
            not in collection_names
        ):

            raise RuntimeError(
                f"Collection "
                f"'{self.collection_name}' "
                f"not found.\n\n"
                f"Available collections: "
                f"{collection_names}"
            )

        self.collection = (
            self.chroma_client
            .get_collection(
                name=self.collection_name
            )
        )

        collection_count = (
            self.collection.count()
        )

        logger.info(
            f"Using collection: "
            f"{self.collection_name}"
        )

        logger.info(
            f"Vector chunks: "
            f"{collection_count}"
        )

        if collection_count == 0:

            raise RuntimeError(
                "The ChromaDB collection "
                "is empty."
            )

        # ----------------------------------------------------
        # BM25
        # ----------------------------------------------------

        self.bm25_chunks: List[
            Dict[str, Any]
        ] = []

        self.bm25: Optional[
            BM25Okapi
        ] = None

        self._load_chunks_for_bm25()

        # ----------------------------------------------------
        # READY
        # ----------------------------------------------------

        print("\n" + "=" * 70)
        print(
            "HYBRID RETRIEVER READY"
        )
        print("=" * 70)

        logger.info(
            f"Vector chunks: "
            f"{collection_count}"
        )

        logger.info(
            f"BM25 chunks: "
            f"{len(self.bm25_chunks)}"
        )


    # ========================================================
    # LOAD BM25 DATA
    # ========================================================

    def _load_chunks_for_bm25(
        self
    ) -> None:

        logger.info(
            "Loading chunks for BM25..."
        )

        json_files = sorted(
            self.chunks_dir.rglob(
                "*.json"
            )
        )

        if not json_files:

            raise FileNotFoundError(
                "No JSON files found in:\n"
                f"{self.chunks_dir}"
            )

        all_chunks: List[
            Dict[str, Any]
        ] = []

        seen_ids = set()

        # ----------------------------------------------------
        # LOAD FILES
        # ----------------------------------------------------

        for json_file in json_files:

            logger.info(
                f"Processing: "
                f"{json_file.name}"
            )

            try:

                with open(
                    json_file,
                    "r",
                    encoding="utf-8"
                ) as file:

                    data = json.load(
                        file
                    )

            except json.JSONDecodeError as error:

                logger.warning(
                    f"Skipping invalid JSON: "
                    f"{json_file.name}"
                )

                logger.warning(
                    str(error)
                )

                continue

            except Exception as error:

                logger.warning(
                    f"Could not read: "
                    f"{json_file.name}"
                )

                logger.warning(
                    str(error)
                )

                continue

            # ------------------------------------------------
            # EXTRACT CHUNKS
            # ------------------------------------------------

            raw_chunks = (
                extract_chunks_from_data(
                    data
                )
            )

            logger.info(
                f"Found "
                f"{len(raw_chunks)} "
                f"candidate chunks."
            )

            # ------------------------------------------------
            # PROCESS CHUNKS
            # ------------------------------------------------

            for local_index, raw_chunk in enumerate(
                raw_chunks
            ):

                if not isinstance(
                    raw_chunk,
                    dict
                ):

                    continue

                content = (
                    get_chunk_content(
                        raw_chunk
                    )
                )

                if not content:

                    continue

                fallback_id = (
                    f"{json_file.stem}"
                    f"_chunk_"
                    f"{local_index:05d}"
                )

                chunk_id = (
                    get_chunk_id(
                        raw_chunk,
                        fallback_id
                    )
                )

                # ------------------------------------------------
                # ENSURE UNIQUE ID
                # ------------------------------------------------

                original_chunk_id = (
                    chunk_id
                )

                counter = 1

                while chunk_id in seen_ids:

                    chunk_id = (
                        f"{original_chunk_id}"
                        f"_{counter}"
                    )

                    counter += 1

                seen_ids.add(
                    chunk_id
                )

                metadata = (
                    extract_metadata(
                        raw_chunk
                    )
                )

                # Add source information
                metadata.setdefault(
                    "source_file",
                    json_file.name
                )

                processed_chunk = {

                    "chunk_id":
                        chunk_id,

                    "content":
                        content,

                    "metadata":
                        metadata
                }

                all_chunks.append(
                    processed_chunk
                )

        # ----------------------------------------------------
        # VALIDATE
        # ----------------------------------------------------

        if not all_chunks:

            raise RuntimeError(
                "No valid chunks could be "
                "loaded for BM25."
            )

        self.bm25_chunks = (
            all_chunks
        )

        logger.info(
            f"Total BM25 chunks: "
            f"{len(self.bm25_chunks)}"
        )

        # ----------------------------------------------------
        # TOKENIZE
        # ----------------------------------------------------

        tokenized_corpus = [

            tokenize(
                chunk[
                    "content"
                ]
            )

            for chunk
            in self.bm25_chunks

        ]

        # ----------------------------------------------------
        # BM25 INDEX
        # ----------------------------------------------------

        logger.info(
            "Building BM25 index..."
        )

        self.bm25 = BM25Okapi(
            tokenized_corpus
        )

        logger.info(
            "BM25 index ready."
        )


    # ========================================================
    # DENSE RETRIEVAL
    # ========================================================

    def retrieve_dense(

        self,

        query: str,

        top_k: int = 10

    ) -> List[
        Dict[str, Any]
    ]:

        if not query:

            return []

        query = query.strip()

        if not query:

            return []

        logger.info(
            f"Dense retrieval for: "
            f"{query}"
        )

        query_embedding = (
            self.embedder.encode(
                query,
                normalize_embeddings=True
            )
        )

        collection_count = (
            self.collection.count()
        )

        n_results = min(
            max(
                1,
                top_k
            ),
            collection_count
        )

        results = (
            self.collection.query(

                query_embeddings=[
                    query_embedding.tolist()
                ],

                n_results=n_results,

                include=[
                    "documents",
                    "metadatas",
                    "distances"
                ]

            )
        )

        ids = results.get(
            "ids",
            [[]]
        )[0]

        documents = results.get(
            "documents",
            [[]]
        )[0]

        metadatas = results.get(
            "metadatas",
            [[]]
        )[0]

        distances = results.get(
            "distances",
            [[]]
        )[0]

        retrieved = []

        for index, chunk_id in enumerate(
            ids
        ):

            document = ""

            metadata = {}

            distance = 0.0

            if index < len(
                documents
            ):

                document = (
                    documents[index]
                    or ""
                )

            if index < len(
                metadatas
            ):

                metadata = (
                    metadatas[index]
                    or {}
                )

            if index < len(
                distances
            ):

                distance = float(
                    distances[index]
                )

            # Chroma distance:
            # smaller = more similar

            dense_score = 1.0 / (
                1.0 + distance
            )

            retrieved.append(

                {

                    "chunk_id":
                        str(
                            chunk_id
                        ),

                    "content":
                        document,

                    "metadata":
                        metadata,

                    "dense_score":
                        dense_score,

                    "distance":
                        distance

                }

            )

        return retrieved


    # ========================================================
    # BM25 RETRIEVAL
    # ========================================================

    def retrieve_bm25(

        self,

        query: str,

        top_k: int = 10

    ) -> List[
        Dict[str, Any]
    ]:

        if not query:

            return []

        query = query.strip()

        if not query:

            return []

        if self.bm25 is None:

            return []

        query_tokens = tokenize(
            query
        )

        if not query_tokens:

            return []

        scores = np.asarray(

            self.bm25.get_scores(
                query_tokens
            ),

            dtype=float

        )

        if len(scores) == 0:

            return []

        top_k = min(
            max(
                1,
                top_k
            ),
            len(
                self.bm25_chunks
            )
        )

        top_indices = np.argsort(
            scores
        )[
            -top_k:
        ][
            ::-1
        ]

        retrieved = []

        for index in top_indices:

            index = int(
                index
            )

            chunk = (
                self.bm25_chunks[
                    index
                ]
            )

            retrieved.append(

                {

                    "chunk_id":
                        chunk[
                            "chunk_id"
                        ],

                    "content":
                        chunk[
                            "content"
                        ],

                    "metadata":
                        chunk.get(
                            "metadata",
                            {}
                        ),

                    "bm25_score":
                        float(
                            scores[
                                index
                            ]
                        )

                }

            )

        return retrieved


    # ========================================================
    # HYBRID RETRIEVAL
    # ========================================================

    def hybrid_retrieve(

        self,

        query: str,

        top_k: int = 10,

        dense_k: int = 15,

        bm25_k: int = 15

    ) -> List[
        Dict[str, Any]
    ]:

        if not query or not query.strip():

            return []

        logger.info(
            f"Hybrid retrieval for: "
            f"{query}"
        )

        # ----------------------------------------------------
        # DENSE
        # ----------------------------------------------------

        dense_results = (
            self.retrieve_dense(

                query=query,

                top_k=dense_k

            )
        )

        # ----------------------------------------------------
        # BM25
        # ----------------------------------------------------

        bm25_results = (
            self.retrieve_bm25(

                query=query,

                top_k=bm25_k

            )
        )

        # ----------------------------------------------------
        # NORMALIZE DENSE
        # ----------------------------------------------------

        dense_scores = [

            result.get(
                "dense_score",
                0.0
            )

            for result
            in dense_results

        ]

        normalized_dense = (
            normalize_scores(
                dense_scores
            )
        )

        # ----------------------------------------------------
        # NORMALIZE BM25
        # ----------------------------------------------------

        bm25_scores = [

            result.get(
                "bm25_score",
                0.0
            )

            for result
            in bm25_results

        ]

        normalized_bm25 = (
            normalize_scores(
                bm25_scores
            )
        )

        # ----------------------------------------------------
        # MERGE RESULTS
        # ----------------------------------------------------

        result_map: Dict[
            str,
            Dict[str, Any]
        ] = {}

        # Dense results

        for index, result in enumerate(
            dense_results
        ):

            chunk_id = str(
                result[
                    "chunk_id"
                ]
            )

            result_map[
                chunk_id
            ] = {

                **result,

                "dense_score":

                    float(
                        normalized_dense[
                            index
                        ]
                    ),

                "bm25_score":

                    0.0

            }

        # BM25 results

        for index, result in enumerate(
            bm25_results
        ):

            chunk_id = str(
                result[
                    "chunk_id"
                ]
            )

            bm25_score = float(
                normalized_bm25[
                    index
                ]
            )

            if chunk_id in result_map:

                result_map[
                    chunk_id
                ][
                    "bm25_score"
                ] = bm25_score

            else:

                result_map[
                    chunk_id
                ] = {

                    **result,

                    "dense_score":

                        0.0,

                    "bm25_score":

                        bm25_score

                }

        # ----------------------------------------------------
        # FUSION
        # ----------------------------------------------------

        hybrid_results = []

        for result in (
            result_map.values()
        ):

            dense_score = float(

                result.get(
                    "dense_score",
                    0.0
                )

            )

            bm25_score = float(

                result.get(
                    "bm25_score",
                    0.0
                )

            )

            hybrid_score = (

                self.alpha
                * dense_score

            ) + (

                (
                    1.0
                    - self.alpha
                )
                * bm25_score

            )

            result[
                "hybrid_score"
            ] = hybrid_score

            hybrid_results.append(
                result
            )

        # ----------------------------------------------------
        # SORT
        # ----------------------------------------------------

        hybrid_results.sort(

            key=lambda item:

                item.get(
                    "hybrid_score",
                    0.0
                ),

            reverse=True

        )

        return hybrid_results[
            :top_k
        ]


    # ========================================================
    # DEDUPLICATION
    # ========================================================

    def deduplicate(

        self,

        results: List[
            Dict[str, Any]
        ]

    ) -> List[
        Dict[str, Any]
    ]:
        """
        Generic deduplication.

        Preference order:

        1. document_id + section
        2. source_file + section
        3. chunk_id
        """

        seen = set()

        unique_results = []

        for result in results:

            metadata = (
                result.get(
                    "metadata",
                    {}
                )
                or {}
            )

            document_id = (
                metadata.get(
                    "document_id"
                )
            )

            source_file = (
                metadata.get(
                    "source_file"
                )
            )

            section = (

                metadata.get(
                    "section"
                )

                or

                metadata.get(
                    "heading"
                )

                or

                metadata.get(
                    "title"
                )

            )

            chunk_id = (
                result.get(
                    "chunk_id"
                )
            )

            # ------------------------------------------------
            # CREATE DEDUP KEY
            # ------------------------------------------------

            if document_id and section:

                key = (
                    "document_section",

                    str(
                        document_id
                    ),

                    str(
                        section
                    )

                )

            elif source_file and section:

                key = (
                    "source_section",

                    str(
                        source_file
                    ),

                    str(
                        section
                    )

                )

            else:

                key = (
                    "chunk",

                    str(
                        chunk_id
                    )

                )

            if key in seen:

                continue

            seen.add(
                key
            )

            unique_results.append(
                result
            )

        return unique_results


    # ========================================================
    # MAIN RETRIEVAL
    # ========================================================

    def retrieve(

        self,

        query: str,

        top_k: int = 5,

        dense_k: int = 15,

        bm25_k: int = 15,

        deduplicate: bool = True

    ) -> List[
        Dict[str, Any]
    ]:
        """
        Main retrieval entry point.

        Designed for:

        Retriever
            ↓
        Cross Encoder Reranker
            ↓
        LLM Generator
        """

        if not query or not query.strip():

            return []

        # Get extra candidates because
        # deduplication may remove some.

        candidate_count = max(
            top_k * 3,
            top_k
        )

        results = (
            self.hybrid_retrieve(

                query=query,

                top_k=candidate_count,

                dense_k=dense_k,

                bm25_k=bm25_k

            )
        )

        if deduplicate:

            results = (
                self.deduplicate(
                    results
                )
            )

        return results[
            :top_k
        ]
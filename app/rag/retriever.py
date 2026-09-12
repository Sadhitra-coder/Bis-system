"""
app/rag/retriever.py

Phase 4 Hybrid Retriever — RRF fusion + identifier boosting + canonical RetrievalResult.

ARCHITECTURE
------------
Query
  → normalize_query()          (app/rag/query.py)
  → extract_query_entities()   (app/rag/query.py)
  → Dense retrieval            (SentenceTransformer → Chroma)
  → BM25 retrieval             (BM25Okapi over same Chroma rows)
  → Reciprocal Rank Fusion     (replaces min-max weighted alpha)
  → Identifier / knowledge boost
  → [CrossEncoder reranking]   (in rag/pipeline.py)
  → List[RetrievalResult]

SINGLE SOURCE OF TRUTH
-----------------------
Both retrieval paths read from the same ChromaDB collection.
The BM25 corpus is built from collection.get() — not from filesystem JSON.
See module docstring note in original retriever for full rationale.

RRF vs MIN-MAX FUSION
---------------------
The previous implementation normalized raw scores to [0,1] and combined
them with weighted alpha. This assumes the score distributions are
comparable across queries — they are not. BM25 scores depend on
collection-wide IDF; cosine similarity depends on the embedding space.

Reciprocal Rank Fusion (Cormack et al., 2009) operates on ranks, not
scores. It is parameter-stable and distribution-agnostic:

    RRF(d) = sum_r 1 / (k + rank_r(d))

where k is a smoothing constant (default 60, per original paper).

IDENTIFIER BOOSTING
-------------------
After RRF, a controlled additive boost is applied to candidates whose
metadata exactly matches extracted query entities. Boost amounts are
fractional (0.10–0.20) relative to the maximum RRF score in the pool.
This ensures identifier matches are prioritized but strong semantic
candidates are not eliminated.

DETERMINISM
-----------
Tie-breaking uses chunk_id (lexicographic) for stability.
No set/dict iteration order affects ranking.
"""

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from app.config import VECTOR_DB_DIR, settings
from app.index_schema import decode_optional_int
from app.rag.query import (
    QueryEntities,
    RetrievalResult,
    RetrievalTrace,
    normalize_query,
    extract_query_entities,
    build_chroma_filters,
)


logger = logging.getLogger(__name__)

DEFAULT_COLLECTION_NAME = settings.CHROMA_COLLECTION_NAME
DEFAULT_EMBEDDING_MODEL = settings.EMBEDDING_MODEL
DEFAULT_ALPHA = settings.HYBRID_ALPHA
CORPUS_PAGE_SIZE = 1000

# Identifier boost values from config
_BOOST_STANDARD = settings.IDENTIFIER_BOOST_STANDARD
_BOOST_STANDARD_YEAR = settings.IDENTIFIER_BOOST_STANDARD_YEAR
_BOOST_CLAUSE = settings.IDENTIFIER_BOOST_CLAUSE
_BOOST_AMENDMENT = settings.IDENTIFIER_BOOST_AMENDMENT


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def tokenize(text: str) -> List[str]:
    """
    BM25 tokenizer.
    Handles words, numbers, dotted terms (clause numbers), hyphenated terms,
    and Unicode scripts (Devanagari, Hindi, etc.) with combining characters.
    """
    if not text:
        return []
    
    # Split by whitespace, then strip leading/trailing punctuation from each token
    punct_to_strip = ' \t\n\r"\'`.,;:!?()[]{}<>/\\|~@#$%^&*+=_'
    tokens = []
    for raw in str(text).lower().split():
        cleaned = raw.strip(punct_to_strip)
        if cleaned:
            tokens.append(cleaned)
    return tokens



def normalize_scores(scores: List[float]) -> np.ndarray:
    """
    Min-max normalize scores to [0, 1].
    Retained for backward compatibility with any callers outside the main pipeline.
    The main fusion path now uses RRF instead of min-max.
    """
    if not scores:
        return np.array([], dtype=float)
    arr = np.asarray(scores, dtype=float)
    mn, mx = arr.min(), arr.max()
    if np.isclose(mn, mx):
        return np.zeros(len(arr)) if np.isclose(mx, 0.0) else np.ones(len(arr))
    return (arr - mn) / (mx - mn)


def reciprocal_rank_fusion(
    ranked_lists: List[List[str]],
    k: int = 60,
) -> Dict[str, float]:
    """
    Reciprocal Rank Fusion over multiple ranked lists of chunk_ids.

    RRF(d) = Σ_r  1 / (k + rank_r(d))

    Parameters
    ----------
    ranked_lists : list of lists of chunk_id strings, each already ordered best→worst
    k            : RRF smoothing constant (default 60 per Cormack et al. 2009)

    Returns
    -------
    dict mapping chunk_id → RRF score (higher is better)
    """
    scores: Dict[str, float] = {}
    for ranked in ranked_lists:
        for rank_0, chunk_id in enumerate(ranked):
            rank_1 = rank_0 + 1  # 1-based
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank_1)
    return scores


def build_retrieval_result(
    chunk: Dict[str, Any],
    fusion_score: float = 0.0,
    dense_rank: Optional[int] = None,
    bm25_rank: Optional[int] = None,
    retrieval_methods: Optional[List[str]] = None,
    identifier_match: bool = False,
) -> RetrievalResult:
    """
    Construct a canonical RetrievalResult from a raw Chroma/BM25 chunk dict.
    Provenance is never discarded.
    """
    meta = chunk.get("metadata") or {}

    def _str(key: str) -> str:
        v = chunk.get(key) or meta.get(key)
        return str(v) if v is not None else ""

    def _int_or_none(key: str) -> Optional[int]:
        # The index encodes an unknown integer as UNKNOWN_INT (-1), because
        # Chroma cannot store null and 0 is a legitimate offset. Decoding
        # that back to None here is what keeps "page unknown" distinct from
        # "page 0" all the way out to the API.
        return decode_optional_int(chunk.get(key) or meta.get(key))

    def _opt_str(key: str) -> Optional[str]:
        v = chunk.get(key) or meta.get(key)
        return str(v) if v is not None and v != "" and v != 0 else None

    # heading_context: stored as JSON string in Chroma, or as list
    hc = chunk.get("heading_context") or meta.get("heading_context") or []
    if isinstance(hc, str):
        try:
            import json
            hc = json.loads(hc)
        except Exception:
            hc = [hc] if hc else []

    raw_content = chunk.get("content", "") or ""
    source_content = meta.get("source_content") or chunk.get("source_content") or raw_content
    contextualized_content = meta.get("contextualized_content") or chunk.get("contextualized_content") or raw_content
    context_method = meta.get("context_generation_method") or chunk.get("context_generation_method")
    context_version = meta.get("context_generation_version") or chunk.get("context_generation_version")

    return RetrievalResult(
        chunk_id=_str("chunk_id"),
        document_id=_str("document_id"),
        source_hash=_str("source_hash"),
        source_file=_str("source_file"),
        page_start=_int_or_none("page_start"),
        page_end=_int_or_none("page_end"),
        content=source_content,
        source_content=source_content,
        contextualized_content=contextualized_content,
        context_generation_method=context_method,
        context_generation_version=context_version,
        section=_str("section"),
        heading_context=list(hc),
        clause_id=_opt_str("clause_id"),
        clause_title=_opt_str("clause_title"),
        standard_id=_opt_str("standard_id"),
        standard_number=_opt_str("standard_number"),
        standard_title=_opt_str("standard_title"),
        version_id=_opt_str("version_id"),
        edition_or_version=_opt_str("edition_or_version"),
        amendment_number=_opt_str("amendment_number"),
        standard_year=_int_or_none("standard_year"),
        part_number=_opt_str("part_number"),
        authority=_opt_str("authority"),
        document_type=_opt_str("document_type"),
        source_url=_opt_str("source_url"),
        retrieval_methods=list(retrieval_methods or []),
        dense_rank=dense_rank,
        bm25_rank=bm25_rank,
        identifier_match=identifier_match,
        fusion_score=fusion_score,
        reranker_score=None,
        metadata=dict(meta),
    )



# ============================================================
# IDENTIFIER BOOSTING
# ============================================================

def apply_identifier_boost(
    candidates: List[RetrievalResult],
    entities: QueryEntities,
) -> List[RetrievalResult]:
    """
    Apply controlled additive boosts to candidates whose metadata
    matches extracted query entities.

    Boost strategy (documented semantics):
        - exact standard_number match  → +BOOST_STANDARD of max_rrf_score
        - standard_number + year match → additional +BOOST_STANDARD_YEAR
        - exact clause_id match        → +BOOST_CLAUSE
        - exact amendment_number match → +BOOST_AMENDMENT

    Max total boost ≤ BOOST_STANDARD + BOOST_STANDARD_YEAR + BOOST_CLAUSE + BOOST_AMENDMENT
    (currently ≤ 0.55 of max_rrf_score).

    The boost is proportional to the max score in the pool, so it never
    swamps a semantically stronger candidate from an unrelated document.
    """
    if not entities.has_any() or not candidates:
        return candidates

    max_score = max(c.fusion_score for c in candidates) if candidates else 1.0
    if max_score <= 0.0:
        max_score = 1.0

    for c in candidates:
        boost = 0.0
        meta_std = (c.standard_number or "").strip().upper()
        query_std = (entities.standard_number or "").strip().upper()
        meta_clause = (c.clause_id or "").strip()
        meta_amd = (c.amendment_number or "").strip()
        meta_year = c.standard_year

        # Standard number match
        if query_std and meta_std == query_std:
            boost += _BOOST_STANDARD * max_score
            c.identifier_match = True
            # Standard + year combo
            if entities.standard_year and meta_year == entities.standard_year:
                boost += _BOOST_STANDARD_YEAR * max_score

        # Clause match
        if entities.clause_id and meta_clause == entities.clause_id.strip():
            boost += _BOOST_CLAUSE * max_score
            c.identifier_match = True

        # Amendment match
        if entities.amendment_number and meta_amd == entities.amendment_number.strip():
            boost += _BOOST_AMENDMENT * max_score
            c.identifier_match = True

        if boost > 0.0:
            c.fusion_score += boost
            if "identifier" not in c.retrieval_methods:
                c.retrieval_methods.append("identifier")

    # Re-sort after boost with deterministic tie-break
    candidates.sort(key=lambda c: (-c.fusion_score, c.chunk_id))
    return candidates


# ============================================================
# HYBRID RETRIEVER
# ============================================================

class HybridRetriever:
    """
    Phase 4 Hybrid Retriever.

    Pipeline per query:
        normalize → entities → [Chroma hard filter] →
        dense(top_k) + BM25(top_k) →
        RRF fusion → identifier boost →
        RetrievalResult list

    RRF replaces the previous min-max weighted alpha fusion.
    Identifier boosting adds controlled post-fusion ranking signals.
    """

    def __init__(
        self,
        persist_directory: Optional[str] = None,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        alpha: float = DEFAULT_ALPHA,
        embedder: Optional[SentenceTransformer] = None,
        collection: Optional[Any] = None,
        rrf_k: int = settings.RRF_K,
        candidate_limit: int = settings.RRF_CANDIDATE_LIMIT,
    ):
        self.alpha = alpha
        self.collection_name = collection_name
        self.embedding_model = embedding_model
        self.rrf_k = rrf_k
        self.candidate_limit = candidate_limit

        # Vector database
        if persist_directory:
            self.persist_directory = Path(persist_directory).resolve()
        else:
            self.persist_directory = VECTOR_DB_DIR

        if collection is not None:
            self.collection = collection
            self.chroma_client = None
        else:
            if not self.persist_directory.exists():
                raise FileNotFoundError(
                    f"Vector database directory not found: {self.persist_directory}"
                )
            logger.info("Connecting to ChromaDB at %s", self.persist_directory)
            self.chroma_client = chromadb.PersistentClient(path=str(self.persist_directory))
            existing = [item.name for item in self.chroma_client.list_collections()]
            if self.collection_name not in existing:
                raise RuntimeError(
                    f"Collection '{self.collection_name}' not found. "
                    f"Available: {existing}. Ingest a document first."
                )
            self.collection = self.chroma_client.get_collection(name=self.collection_name)

        collection_count = self.collection.count()
        if collection_count == 0:
            raise RuntimeError("ChromaDB collection is empty. Ingest at least one document first.")

        # Embedding model
        self.embedder = embedder or SentenceTransformer(embedding_model)

        # BM25 index (built from same Chroma rows)
        self.bm25_chunks: List[Dict[str, Any]] = []
        self.bm25: Optional[BM25Okapi] = None
        self.build_bm25_index()

        logger.info(
            "HybridRetriever ready | chunks=%d | BM25=%d | rrf_k=%d",
            collection_count, len(self.bm25_chunks), self.rrf_k,
        )

    # -------------------------------------------------------
    # BM25 INDEX
    # -------------------------------------------------------

    def _read_corpus_from_collection(self) -> List[Dict[str, Any]]:
        """
        Page through the Chroma collection to build BM25 corpus.
        Both retrieval paths share the same chunk_ids — essential for RRF fusion.
        Does NOT load the entire collection in one call.
        """
        total = self.collection.count()
        corpus: List[Dict[str, Any]] = []
        offset = 0

        while offset < total:
            page = self.collection.get(
                limit=CORPUS_PAGE_SIZE,
                offset=offset,
                include=["documents", "metadatas"],
            )
            ids = page.get("ids") or []
            documents = page.get("documents") or []
            metadatas = page.get("metadatas") or []

            if not ids:
                break  # defensive against Chroma versions ignoring offset

            for i, chunk_id in enumerate(ids):
                content = documents[i] if i < len(documents) else ""
                content = content or ""
                if not content.strip():
                    continue
                metadata = metadatas[i] if i < len(metadatas) else {}
                corpus.append({
                    "chunk_id": str(chunk_id),
                    "content": content,
                    "metadata": dict(metadata or {}),
                })
            offset += len(ids)

        return corpus

    def build_bm25_index(self) -> None:
        """
        (Re)build the lexical BM25 index from the Chroma collection.
        Call after ingesting new documents in the same process.
        """
        logger.info("Building BM25 index from Chroma collection...")
        corpus = self._read_corpus_from_collection()
        if not corpus:
            raise RuntimeError(f"No documents found in collection '{self.collection_name}'.")
        self.bm25_chunks = corpus
        tokenized = [tokenize(c["content"]) for c in self.bm25_chunks]
        self.bm25 = BM25Okapi(tokenized)
        logger.info("BM25 index ready | chunks=%d", len(self.bm25_chunks))

    # -------------------------------------------------------
    # DENSE RETRIEVAL
    # -------------------------------------------------------

    def retrieve_dense(
        self,
        query: str,
        top_k: int = 15,
        where: Optional[Dict] = None,
    ) -> List[Dict[str, Any]]:
        """
        Dense vector retrieval via Chroma.

        Parameters
        ----------
        query   : normalized query string
        top_k   : candidates to fetch
        where   : Chroma metadata filter (hard constraint)

        Returns list of dicts with chunk_id, content, metadata, dense_score, dense_rank.
        """
        if not query or not query.strip():
            return []

        query_embedding = self.embedder.encode(query.strip(), normalize_embeddings=True)
        n_results = min(max(1, top_k), self.collection.count())

        kwargs: Dict[str, Any] = {
            "query_embeddings": [query_embedding.tolist()],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        try:
            results = self.collection.query(**kwargs)
        except Exception as e:
            logger.warning("Dense retrieval failed: %s", e)
            return []

        ids = (results.get("ids") or [[]])[0]
        documents = (results.get("documents") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]

        retrieved = []
        for i, chunk_id in enumerate(ids):
            doc = documents[i] if i < len(documents) else ""
            meta = metadatas[i] if i < len(metadatas) else {}
            dist = float(distances[i]) if i < len(distances) else 0.0
            retrieved.append({
                "chunk_id": str(chunk_id),
                "content": doc or "",
                "metadata": dict(meta or {}),
                "dense_score": 1.0 / (1.0 + dist),
                "distance": dist,
                "dense_rank": i + 1,
            })
        return retrieved

    # -------------------------------------------------------
    # BM25 RETRIEVAL
    # -------------------------------------------------------

    def retrieve_bm25(
        self,
        query: str,
        top_k: int = 15,
    ) -> List[Dict[str, Any]]:
        """
        BM25 lexical retrieval over the same Chroma corpus.

        Returns list of dicts with chunk_id, content, metadata, bm25_score, bm25_rank.
        Returns [] on empty query or unavailable index.
        """
        if not query or not query.strip() or self.bm25 is None:
            return []

        tokens = tokenize(query.strip())
        if not tokens:
            return []

        scores = np.asarray(self.bm25.get_scores(tokens), dtype=float)
        if len(scores) == 0:
            return []

        top_k = min(max(1, top_k), len(self.bm25_chunks))
        top_indices = np.argsort(scores)[-top_k:][::-1]

        retrieved = []
        for rank_0, idx in enumerate(top_indices):
            idx = int(idx)
            chunk = self.bm25_chunks[idx]
            retrieved.append({
                "chunk_id": chunk["chunk_id"],
                "content": chunk["content"],
                "metadata": chunk.get("metadata", {}),
                "bm25_score": float(scores[idx]),
                "bm25_rank": rank_0 + 1,
            })
        return retrieved

    # -------------------------------------------------------
    # RRF FUSION
    # -------------------------------------------------------

    def fuse_rrf(
        self,
        dense_results: List[Dict[str, Any]],
        bm25_results: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """
        Fuse dense and BM25 candidates with Reciprocal Rank Fusion.

        Each source contributes a ranked list. RRF scores are accumulated
        per chunk_id. Candidates appearing in both lists receive contributions
        from both. Tie-breaking is by chunk_id (lexicographic, deterministic).

        Returns merged candidate dicts sorted by rrf_score descending.
        """
        dense_ranked = [str(r["chunk_id"]) for r in dense_results]
        bm25_ranked = [str(r["chunk_id"]) for r in bm25_results]

        rrf_scores = reciprocal_rank_fusion([dense_ranked, bm25_ranked], k=self.rrf_k)

        # Build lookup maps
        dense_map = {str(r["chunk_id"]): r for r in dense_results}
        bm25_map = {str(r["chunk_id"]): r for r in bm25_results}

        merged: List[Dict[str, Any]] = []
        for chunk_id, rrf_score in rrf_scores.items():
            # Prefer dense's content/metadata (has distance), fall back to BM25
            base = dense_map.get(chunk_id) or bm25_map.get(chunk_id) or {}
            methods = []
            if chunk_id in dense_map:
                methods.append("dense")
            if chunk_id in bm25_map:
                methods.append("bm25")

            merged.append({
                "chunk_id": chunk_id,
                "content": base.get("content", ""),
                "metadata": base.get("metadata", {}),
                "dense_score": dense_map.get(chunk_id, {}).get("dense_score"),
                "bm25_score": bm25_map.get(chunk_id, {}).get("bm25_score"),
                "dense_rank": dense_map.get(chunk_id, {}).get("dense_rank"),
                "bm25_rank": bm25_map.get(chunk_id, {}).get("bm25_rank"),
                "rrf_score": rrf_score,
                "retrieval_methods": methods,
            })

        # Sort: RRF score desc, then chunk_id asc for determinism
        merged.sort(key=lambda x: (-x["rrf_score"], x["chunk_id"]))
        return merged[:top_k]

    # -------------------------------------------------------
    # DEDUPLICATION
    # -------------------------------------------------------

    def deduplicate(self, results: List[Any]) -> List[Any]:
        """Deduplicate by chunk_id. Preserves order."""
        seen: set = set()
        out = []
        for r in results:
            cid = str(getattr(r, "chunk_id", "") or r.get("chunk_id", "") if isinstance(r, dict) else "")
            if cid and cid in seen:
                continue
            seen.add(cid)
            out.append(r)
        return out

    # -------------------------------------------------------
    # MAIN RETRIEVE (canonical entry point)
    # -------------------------------------------------------

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        dense_k: int = 15,
        bm25_k: int = 15,
        deduplicate: bool = True,
        authority: Optional[str] = None,
        document_type: Optional[str] = None,
        return_trace: bool = False,
        strategy: Optional[Any] = None,
        query_context: Optional[Any] = None,
    ) -> Any:
        """
        Full retrieval pipeline returning List[RetrievalResult].

        Stages (all observable via return_trace=True):
            1. Query normalization
            2. Entity extraction
            3. Hard Chroma filter construction
            4. Dense retrieval
            5. BM25 retrieval
            6. RRF fusion
            7. Identifier/knowledge boost
            8. [CrossEncoder reranking is done in RAGPipeline, not here]

        Parameters
        ----------
        query          : raw user query (preserved; normalization applied internally)
        top_k          : maximum results to return after fusion + boost
        dense_k        : dense candidates to fetch
        bm25_k         : BM25 candidates to fetch
        deduplicate    : remove duplicate chunk_ids (default True)
        authority      : optional hard Chroma filter
        document_type  : optional hard Chroma filter
        return_trace   : if True, return (results, trace) tuple
        strategy       : operational retrieval strategy from query intelligence
        query_context  : canonical QueryContext object

        Returns
        -------
        List[RetrievalResult]  (or (List[RetrievalResult], RetrievalTrace) if return_trace)
        """
        t0 = time.perf_counter()
        strat_val = None
        if strategy is not None:
            strat_val = strategy.value if hasattr(strategy, "value") else str(strategy)
        elif query_context is not None and hasattr(query_context, "retrieval_strategy"):
            strat_val = query_context.retrieval_strategy.value if hasattr(query_context.retrieval_strategy, "value") else str(query_context.retrieval_strategy)
        self.last_strategy = strat_val

        trace = RetrievalTrace(
            original_query=query,
            strategy=strat_val,
            contextual_representation_used=bool(getattr(settings, "ENABLE_CONTEXTUAL_RETRIEVAL", True)),
            context_mode=getattr(settings, "CONTEXT_GENERATION_METHOD", "structural"),
            context_generation_version=getattr(settings, "CONTEXT_GENERATION_VERSION", "1.0"),
        )


        # ---- Empty / whitespace guard ----
        if not query or not query.strip():
            logger.warning("retrieve() called with empty query.")
            if return_trace:
                return [], trace
            return []

        # ---- 1. Normalize ----
        normalized = normalize_query(query)
        trace.normalized_query = normalized

        # ---- 2. Entity extraction ----
        entities = extract_query_entities(normalized)
        trace.entities = entities.to_dict()

        # ---- 3. Hard filters ----
        where = build_chroma_filters(authority=authority, document_type=document_type)
        trace.filters = where or {}

        # ---- 4. Dense retrieval ----
        t1 = time.perf_counter()
        dense_results = self.retrieve_dense(query=normalized, top_k=dense_k, where=where)
        trace.dense_latency_ms = (time.perf_counter() - t1) * 1000
        trace.dense_candidates = [{"chunk_id": r["chunk_id"], "dense_rank": r.get("dense_rank")} for r in dense_results]

        # ---- 5. BM25 retrieval ----
        t2 = time.perf_counter()
        bm25_results = self.retrieve_bm25(query=normalized, top_k=bm25_k)
        trace.bm25_latency_ms = (time.perf_counter() - t2) * 1000
        trace.bm25_candidates = [{"chunk_id": r["chunk_id"], "bm25_rank": r.get("bm25_rank")} for r in bm25_results]

        # ---- 6. RRF fusion ----
        t3 = time.perf_counter()
        candidate_limit = min(self.candidate_limit, max(top_k * 3, 30))
        fused = self.fuse_rrf(dense_results, bm25_results, top_k=candidate_limit)
        trace.rrf_latency_ms = (time.perf_counter() - t3) * 1000
        trace.rrf_candidates = [{"chunk_id": r["chunk_id"], "rrf_score": r["rrf_score"]} for r in fused]

        # ---- 7. Build RetrievalResult objects ----
        results: List[RetrievalResult] = []
        for r in fused:
            rr = build_retrieval_result(
                chunk=r,
                fusion_score=r["rrf_score"],
                dense_rank=r.get("dense_rank"),
                bm25_rank=r.get("bm25_rank"),
                retrieval_methods=r.get("retrieval_methods", []),
                identifier_match=False,
            )
            results.append(rr)

        # ---- 8. Identifier boost ----
        t4 = time.perf_counter()
        if entities.has_any():
            results = apply_identifier_boost(results, entities)
        trace.boost_latency_ms = (time.perf_counter() - t4) * 1000
        trace.boosted_candidates = [{"chunk_id": r.chunk_id, "fusion_score": r.fusion_score, "identifier_match": r.identifier_match} for r in results]

        # ---- 9. Deduplicate and trim ----
        if deduplicate:
            results = self.deduplicate(results)
        results = results[:top_k]

        trace.total_latency_ms = (time.perf_counter() - t0) * 1000

        if return_trace:
            return results, trace
        return results

    # -------------------------------------------------------
    # BACKWARD-COMPATIBLE hybrid_retrieve (dict output)
    # -------------------------------------------------------

    def hybrid_retrieve(
        self,
        query: str,
        top_k: int = 10,
        dense_k: int = 15,
        bm25_k: int = 15,
    ) -> List[Dict[str, Any]]:
        """
        Backward-compatible entry point returning list of dicts.
        Delegates to retrieve() and converts RetrievalResult → dict.
        """
        results = self.retrieve(query=query, top_k=top_k, dense_k=dense_k, bm25_k=bm25_k)
        return [r.to_dict() for r in results]


# ============================================================
# EVALUATION UTILITIES
# ============================================================

def recall_at_k(expected_ids: List[str], retrieved_ids: List[str], k: int) -> float:
    """Recall@K: fraction of expected IDs found in top-K retrieved."""
    if not expected_ids:
        return 0.0
    top_k_set = set(retrieved_ids[:k])
    hits = sum(1 for eid in expected_ids if eid in top_k_set)
    return hits / len(expected_ids)


def mrr_at_k(expected_ids: List[str], retrieved_ids: List[str], k: int) -> float:
    """MRR@K: reciprocal rank of first relevant result in top-K."""
    expected_set = set(expected_ids)
    for i, rid in enumerate(retrieved_ids[:k]):
        if rid in expected_set:
            return 1.0 / (i + 1)
    return 0.0


def evaluate_retrieval(
    retriever: "HybridRetriever",
    eval_dataset: List[Dict[str, Any]],
    k_values: Optional[List[int]] = None,
) -> Dict[str, float]:
    """
    Evaluate retrieval against a manually curated evaluation dataset.

    Dataset format:
        [{"query": "...", "expected_chunk_ids": ["chunk_id_1", ...]}, ...]

    Returns:
        {"Recall@1": ..., "Recall@3": ..., "Recall@5": ..., "MRR@5": ...}

    Does NOT fabricate scores — reports 0.0 for missing chunks honestly.
    """
    if k_values is None:
        k_values = [1, 3, 5]

    recall: Dict[int, List[float]] = {k: [] for k in k_values}
    mrr_scores: List[float] = []

    for item in eval_dataset:
        query = item.get("query", "")
        expected = item.get("expected_chunk_ids", [])
        if not query or not expected:
            continue
        results = retriever.retrieve(query=query, top_k=max(k_values))
        retrieved_ids = [r.chunk_id for r in results]
        for k in k_values:
            recall[k].append(recall_at_k(expected, retrieved_ids, k))
        mrr_scores.append(mrr_at_k(expected, retrieved_ids, max(k_values)))

    metrics: Dict[str, float] = {}
    for k in k_values:
        metrics[f"Recall@{k}"] = float(np.mean(recall[k])) if recall[k] else 0.0
    metrics[f"MRR@{max(k_values)}"] = float(np.mean(mrr_scores)) if mrr_scores else 0.0
    return metrics


# Keep flatten_provenance for any remaining callers
def flatten_provenance(chunk: Dict[str, Any]) -> Dict[str, Any]:
    """
    Promote provenance fields from metadata dict to chunk top level.
    Retained for backward compatibility.
    """
    meta = chunk.get("metadata") or {}
    result = dict(chunk)
    for key in [
        "document_id", "source_file", "page_start", "page_end", "page_number",
        "section", "clause_id", "clause_title", "standard_number", "standard_title",
        "standard_year", "part_number", "amendment_number", "edition_or_version",
        "authority", "document_type", "source_hash", "chunk_index", "content_hash",
        "parser_version",
    ]:
        if key not in result or result[key] is None:
            val = meta.get(key)
            if val is not None and val != "" and val != 0:
                result[key] = val
    if "score" not in result:
        result["score"] = result.get("hybrid_score") or result.get("dense_score") or result.get("bm25_score") or 0.0
    return result

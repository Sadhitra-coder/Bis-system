"""
Retrieval-Augmented Generation pipeline.

COMPONENT OWNERSHIP
-------------------
The retriever, reranker and generator are injected. Each one
owns an expensive resource (BGE-large ~1.3 GB, a cross-encoder,
a BM25 index over the whole collection), so the API builds them
once in the startup hook (app/main.py) and hands them in.
Constructing RAGPipeline() with no arguments still works for
scripts and tests, and then builds its own.

Note: logging is configured by the process entry point (the CLI
or uvicorn), not here. A library module calling basicConfig()
silently hijacks the host application's logging setup.
"""

import logging
import re
from typing import Any, Dict, List, Optional

import time
from app.config import settings
from app.rag.retriever import HybridRetriever
from app.rag.reranker import Reranker
from app.rag.generator import AnswerGenerator
from app.rag.source_format import build_sources
from app.confidence import Decision, EvidenceEvaluator, EvidenceItem
from app.knowledge.repository import KnowledgeRepository, default_repository
from app.grounding import GroundingValidator, GroundingStatus, GroundingResult
from app.rag.query import extract_query_entities, normalize_query
from app.knowledge.models import Standard, StandardVersion, Amendment, ReferenceType, StandardStatus
from app.temporal import (
    CurrentnessResolver,
    TemporalRelationship,
    TemporalRelationshipType,
    TemporalResolution,
    TemporalStatus,
    TemporalTrace,
    extract_temporal_relationships_from_text,
    build_temporal_trace,
)
from app.query_intelligence import build_query_context, QueryContext
from app.query_intelligence.models import QueryIntentType
from app.product_mapping.extractor import extract_product_context
from app.product_mapping.engine import discover_standard_candidates, rank_standard_candidates
from app.product_mapping.models import MappingStatus, ProductContext
from app.technical_specs import (
    TechnicalSpecification,
    TechnicalAnalysisReport,
    extract_technical_specification,
    extract_requirements_from_clause_text,
    analyze_technical_specification,
)
from app.tender_analysis import (
    TenderDocument,
    TenderGapAnalysisReport,
    extract_tender_document,
    analyze_tender_gaps,
)
from app.document_intelligence import (
    BusinessDocument,
    EvidenceGapReport,
    extract_business_document,
    build_evidence_gap_report,
)
from app.applicability import (
    ApplicabilityAssessment,
    ComplianceReadinessReport,
    evaluate_standard_applicability,
    synthesize_compliance_readiness,
)



logger = logging.getLogger(__name__)


# ============================================================
# RAG PIPELINE
# ============================================================

class RAGPipeline:
    """
    Complete Retrieval-Augmented Generation pipeline.

    Flow:

        User Query
            ↓
        Hybrid Retrieval
            ↓
        Candidate Chunks
            ↓
        Cross-Encoder Reranking
            ↓
        Best Context Chunks
            ↓
        Answer Generation
            ↓
        Final Answer
    """

    def __init__(
        self,
        retriever: Optional[HybridRetriever] = None,
        reranker: Optional[Reranker] = None,
        generator: Optional[AnswerGenerator] = None,
        retrieval_top_k: Optional[int] = None,
        dense_k: Optional[int] = None,
        bm25_k: Optional[int] = None,
        rerank_top_k: Optional[int] = None,
        knowledge_repo: Optional[KnowledgeRepository] = None,
    ):

        # ----------------------------------------------------
        # RESOLVE DEFAULTS FROM SETTINGS
        # ----------------------------------------------------

        if retrieval_top_k is None:
            retrieval_top_k = settings.RETRIEVAL_TOP_K

        if dense_k is None:
            dense_k = settings.DENSE_K

        if bm25_k is None:
            bm25_k = settings.BM25_K

        if rerank_top_k is None:
            rerank_top_k = settings.RERANK_TOP_K

        # ----------------------------------------------------
        # VALIDATE CONFIGURATION
        # ----------------------------------------------------

        if retrieval_top_k <= 0:

            raise ValueError(
                "retrieval_top_k must be greater than 0."
            )

        if dense_k <= 0:

            raise ValueError(
                "dense_k must be greater than 0."
            )

        if bm25_k <= 0:

            raise ValueError(
                "bm25_k must be greater than 0."
            )

        if rerank_top_k <= 0:

            raise ValueError(
                "rerank_top_k must be greater than 0."
            )

        # ----------------------------------------------------
        # STORE CONFIGURATION
        # ----------------------------------------------------

        self.retrieval_top_k = (
            retrieval_top_k
        )

        self.dense_k = (
            dense_k
        )

        self.bm25_k = (
            bm25_k
        )

        self.rerank_top_k = (
            rerank_top_k
        )

        self.knowledge_repo = knowledge_repo or default_repository

        # ----------------------------------------------------
        # INITIALIZE RETRIEVER
        # ----------------------------------------------------

        logger.info(
            "Initializing retriever..."
        )

        self.retriever = (
            retriever
            if retriever is not None
            else HybridRetriever()
        )

        # ----------------------------------------------------
        # INITIALIZE RERANKER
        # ----------------------------------------------------

        logger.info(
            "Initializing reranker..."
        )

        self.reranker = (
            reranker
            if reranker is not None
            else Reranker()
        )

        # ----------------------------------------------------
        # INITIALIZE GENERATOR
        # ----------------------------------------------------

        logger.info(
            "Initializing answer generator..."
        )

        if generator is not None:
            self.generator = generator
        elif settings.llm_available:
            self.generator = AnswerGenerator()
        else:
            logger.info("LLM unavailable; running in retrieval-only mode.")
            self.generator = None

        logger.info(
            "RAG pipeline ready | retrieval_top_k=%d | "
            "dense_k=%d | bm25_k=%d | rerank_top_k=%d",
            self.retrieval_top_k,
            self.dense_k,
            self.bm25_k,
            self.rerank_top_k
        )

    # ========================================================
    # BUILD SOURCES
    # ========================================================

    def _build_sources(
        self,
        results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Extract clean, citable source information.

        Delegates to app.rag.source_format, which is also what builds the
        LLM grounding header — one definition of how a standard is named,
        so the citation the model sees and the citation the API returns
        cannot drift apart.
        """
        return build_sources(results)

    # ========================================================
    # EMPTY RESPONSE
    # ========================================================

    def _empty_response(
        self,
        query: str,
        query_context: Optional[QueryContext] = None,
    ) -> Dict[str, Any]:
        """
        Standard response when no relevant information is retrieved.
        """
        if query_context is None:
            query_context = build_query_context(query=query)

        confidence = EvidenceEvaluator.evaluate(
            query=query,
            evidence_items=[],
            repo=self.knowledge_repo,
            query_context=query_context,
        )
        v_reason = confidence.verification_reason
        if query_context.intent.intent == QueryIntentType.STANDARD_DISCOVERY:
            v_reason = "No candidate standards found in the ingested corpus matching the specified product."


        return {
            "query": query,
            "answer": (
                f"Verification Required: {v_reason} "
                "Please verify against authoritative Indian Standard publications."
            ),

            "sources": [],
            "retrieved_chunks": 0,
            "reranked_chunks": 0,
            "model": getattr(
                self.generator,
                "model",
                None
            ),
            "confidence_score": confidence.score,
            "confidence_level": confidence.level.value,
            "decision": confidence.decision.value,
            "query_state": confidence.query_state.value,
            "verification_required": True,
            "verification_reason": v_reason,
            "evidence_summary": "No candidate chunks retrieved.",
            "confidence_trace": confidence.trace,
            "citations": [],
            "claims": [],
            "citation_coverage": 0.0,
            "grounding_status": GroundingStatus.UNVERIFIABLE.value,
            "grounding_reason": "No candidate chunks retrieved to support grounding.",
            "groundedness_score": 0.0,
            # Phase 10 Query Intelligence fields
            "intent": query_context.intent.intent.value,
            "intent_confidence": query_context.intent.intent_confidence,
            "query_context": query_context.to_dict(),
            # Phase 11 Product-to-Standard Candidate Mapping fields
            "product_context": None,
            "candidate_standards": [],
        }


    # ========================================================
    # QUERY PIPELINE
    # ========================================================

    def query(
        self,
        query: str,
        retrieval_top_k: Optional[int] = None,
        rerank_top_k: Optional[int] = None,
        business_context: Optional[Any] = None,
        profile_context: Optional[Any] = None,
        technical_specification: Optional[Any] = None,
        tender_specification: Optional[Any] = None,
        compliance_documents: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """
        Run the complete RAG pipeline.

        Parameters
        ----------

        query:
            User's natural language question.

        retrieval_top_k:
            Optional override for number of retrieved
            candidate chunks.

        rerank_top_k:
            Optional override for number of final
            context chunks.

        Returns
        -------

        {
            "query": "...",
            "answer": "...",
            "sources": [...],
            "retrieved_chunks": ...,
            "reranked_chunks": ...,
            "model": "..."
        }
        """

        # ----------------------------------------------------
        # VALIDATE QUERY
        # ----------------------------------------------------

        if not isinstance(
            query,
            str
        ):

            raise TypeError(
                "Query must be a string."
            )

        query = query.strip()

        if not query:

            raise ValueError(
                "Query cannot be empty."
            )

        # ----------------------------------------------------
        # RESOLVE TOP K VALUES
        # ----------------------------------------------------

        final_retrieval_top_k = (
            retrieval_top_k
            if retrieval_top_k is not None
            else self.retrieval_top_k
        )

        final_rerank_top_k = (
            rerank_top_k
            if rerank_top_k is not None
            else self.rerank_top_k
        )

        if final_retrieval_top_k <= 0:

            raise ValueError(
                "retrieval_top_k must be greater than 0."
            )

        if final_rerank_top_k <= 0:

            raise ValueError(
                "rerank_top_k must be greater than 0."
            )

        logger.info(
            "=" * 60
        )

        logger.info(
            "=" * 60
        )

        logger.info(
            f"Processing query: {query}"
        )

        logger.info(
            "=" * 60
        )

        # ====================================================
        # STEP 0: QUERY INTELLIGENCE (Phase 10)
        # ====================================================

        query_context = build_query_context(
            query=query,
            business_context=business_context,
            profile_context=profile_context,
        )

        # ====================================================
        # STEP 1: RETRIEVAL
        # ====================================================

        logger.info(
            "Step 1/3: Retrieving candidate chunks..."
        )

        retrieval_kwargs: Dict[str, Any] = {
            "query": query,
            "top_k": final_retrieval_top_k,
            "dense_k": self.dense_k,
            "bm25_k": self.bm25_k,
            "deduplicate": False,
        }
        try:
            import inspect
            sig = inspect.signature(self.retriever.retrieve)
            if "strategy" in sig.parameters:
                retrieval_kwargs["strategy"] = query_context.retrieval_strategy
            if "query_context" in sig.parameters:
                retrieval_kwargs["query_context"] = query_context
        except Exception:
            pass

        retrieved_results = self.retriever.retrieve(**retrieval_kwargs)

        logger.info(
            f"Retrieved "
            f"{len(retrieved_results)} "
            f"candidate chunks."
        )

        # ----------------------------------------------------
        # NO RETRIEVAL RESULTS
        # ----------------------------------------------------

        if not retrieved_results:

            logger.warning(
                "No relevant chunks retrieved."
            )

            return self._empty_response(
                query,
                query_context=query_context,
            )

        # ====================================================
        # STEP 2: RERANKING
        # ====================================================

        logger.info(
            "Step 2/3: Reranking candidate chunks..."
        )

        reranked_results = (
            self.reranker.rerank(
                query=query,

                results=
                    retrieved_results,

                top_k=
                    final_rerank_top_k
            )
        )

        logger.info(
            f"Selected "
            f"{len(reranked_results)} "
            f"reranked chunks."
        )

        # ----------------------------------------------------
        # RERANKING FALLBACK
        # ----------------------------------------------------

        # If reranker somehow returns no results,
        # use the best retrieved results.

        if not reranked_results:

            logger.warning(
                "Reranker returned no results. "
                "Using retrieved chunks as fallback."
            )

            reranked_results = (
                retrieved_results[
                    :final_rerank_top_k
                ]
            )

        # Convert reranked chunks to canonical EvidenceItems
        evidence_items = [
            r if isinstance(r, EvidenceItem) else EvidenceItem.from_dict(r)
            for r in reranked_results
        ]

        # Phase 9: Temporal & Version Intelligence
        entities = extract_query_entities(normalize_query(query))
        std_obj: Optional[Standard] = None
        std_versions: List[StandardVersion] = []
        std_amendments: List[Amendment] = []
        std_relationships: List[TemporalRelationship] = []

        if self.knowledge_repo is not None:
            if entities.standard_number:
                std_obj = self.knowledge_repo.get_standard_by_number(entities.standard_number)
                if std_obj:
                    std_versions = self.knowledge_repo.get_standard_versions(std_obj.standard_id)
                    std_amendments = self.knowledge_repo.get_amendments(std_obj.standard_id)
                    # Load any stored temporal relationships for standard and its versions
                    std_relationships = self.knowledge_repo.get_temporal_relationships(std_obj.standard_id)
                    for v in std_versions:
                        for r in self.knowledge_repo.get_temporal_relationships(v.version_id):
                            if not any(existing.relationship_id == r.relationship_id for existing in std_relationships):
                                std_relationships.append(r)

        # Extract explicit temporal statements from retrieved evidence passages
        for ev in evidence_items:
            extracted_rels = extract_temporal_relationships_from_text(
                text=ev.source_content or ev.content,
                source_document_id=ev.document_id,
                source_chunk_ids=[ev.chunk_id],
                source_entity_id=ev.version_id or ev.standard_id or ev.document_id,
            )
            std_relationships.extend(extracted_rels)

        # Build candidate versions from evidence if knowledge repo had none
        if not std_versions and evidence_items:
            seen_years = set()
            for ev in evidence_items:
                meta = getattr(ev, "metadata", {}) or {}
                y = ev.standard_year or meta.get("standard_year")
                v_id = ev.version_id or meta.get("version_id") or (f"ver_{y}" if y else None)
                if v_id and v_id not in seen_years:
                    seen_years.add(v_id)
                    std_versions.append(
                        StandardVersion(
                            version_id=v_id,
                            standard_id=ev.standard_id or meta.get("standard_id") or (entities.standard_number or "std_unknown"),
                            standard_year=y if isinstance(y, int) else None,
                            edition=ev.edition_or_version or meta.get("edition_or_version"),
                            publication_date=meta.get("publication_date"),
                            effective_date=meta.get("effective_date"),
                            withdrawal_date=meta.get("withdrawal_date"),
                            status=StandardStatus.PUBLISHED,
                            document_id=ev.document_id,
                            created_at=time.time(),
                        )
                    )

        # Conservative Currentness Resolution
        temporal_resolution = CurrentnessResolver.resolve(
            standard=std_obj,
            versions=std_versions,
            amendments=std_amendments,
            relationships=std_relationships,
            evidence_items=evidence_items,
            query_temporal_intent=entities.temporal_intent,
            requested_year=entities.standard_year,
            requested_amendment=entities.amendment_number,
        )

        temporal_trace_obj = build_temporal_trace(
            query=query,
            temporal_entities=entities.to_dict(),
            resolution=temporal_resolution,
            relationships=std_relationships,
        )

        # Evaluate evidence confidence & determine operational decision
        confidence = EvidenceEvaluator.evaluate(
            query=query,
            evidence_items=evidence_items,
            repo=self.knowledge_repo,
            temporal_resolution=temporal_resolution,
            query_context=query_context,
        )

        # ====================================================
        # STEP 2.5: PRODUCT-TO-STANDARD CANDIDATE MAPPING (Phase 11)
        # ====================================================
        product_context_obj: Optional[ProductContext] = None
        candidate_standards_list: List[Dict[str, Any]] = []
        candidates: List[Any] = []

        is_product_mapping_intent = (
            query_context.intent.intent in (
                QueryIntentType.STANDARD_DISCOVERY,
                QueryIntentType.APPLICABILITY_QUERY,
            )
            or query_context.business_context.has_explicit_product
        )

        if is_product_mapping_intent:
            product_context_obj = extract_product_context(
                query=query,
                business_context=query_context.business_context,
            )
            if not product_context_obj.is_empty:
                candidates = discover_standard_candidates(
                    product_context=product_context_obj,
                    retriever=self.retriever,
                    reranker=self.reranker,
                    knowledge_repo=self.knowledge_repo,
                    temporal_resolver=CurrentnessResolver,
                    top_k=5,
                    evidence_confidence_level=confidence.level,
                )
                candidate_standards_list = [c.to_dict() for c in candidates]

        # Discover standard requirements from candidates or retrieved evidence
        discovered_reqs = []
        if self.knowledge_repo is not None and candidates:
            for cand in candidates[:3]:
                clauses = self.knowledge_repo.get_standard_clauses(cand.standard_id)
                for cl in clauses:
                    clause_txt = getattr(cl, "clause_text", None) or getattr(cl, "heading_path", None) or getattr(cl, "clause_title", "")
                    r_list = extract_requirements_from_clause_text(
                        clause_text=clause_txt,
                        standard_id=cand.standard_id,
                        standard_number=cand.standard_number,
                        version_id=cl.version_id,
                        clause_id=cl.clause_id,
                        clause_number=cl.clause_number,
                        clause_title=cl.clause_title,
                        temporal_status=cand.temporal_status,
                    )
                    discovered_reqs.extend(r_list)

        if not discovered_reqs and evidence_items:
            for item in evidence_items:
                r_list = extract_requirements_from_clause_text(
                    clause_text=item.content,
                    standard_id=item.standard_number or "evidence",
                    standard_number=item.standard_number,
                    clause_id=item.clause_id,
                    clause_number=item.clause_id,
                    source_chunk_ids=[item.chunk_id],
                    page_start=item.page_start,
                    page_end=item.page_end,
                )
                discovered_reqs.extend(r_list)

        # Phase 12: Technical Specification Analysis
        technical_spec_obj: Optional[TechnicalSpecification] = None
        technical_analysis_report: Optional[TechnicalAnalysisReport] = None

        raw_tech_spec = technical_specification
        if raw_tech_spec is None and query_context.business_context.technical_characteristics:
            raw_tech_spec = query_context.business_context.technical_characteristics

        if raw_tech_spec is not None:
            if isinstance(raw_tech_spec, TechnicalSpecification):
                technical_spec_obj = raw_tech_spec
            elif isinstance(raw_tech_spec, dict):
                technical_spec_obj = extract_technical_specification(
                    structured_dict=raw_tech_spec,
                    product_name=product_context_obj.product_name if product_context_obj else None,
                )
            elif isinstance(raw_tech_spec, str):
                technical_spec_obj = extract_technical_specification(
                    text=raw_tech_spec,
                    product_name=product_context_obj.product_name if product_context_obj else None,
                )

            if discovered_reqs and technical_spec_obj:
                technical_analysis_report = analyze_technical_specification(
                    spec=technical_spec_obj,
                    requirements=discovered_reqs,
                    product_context_id=product_context_obj.product_context_id if product_context_obj else None,
                )

        # Phase 13: Tender / Customer Specification Gap Analysis
        tender_doc_obj: Optional[TenderDocument] = None
        tender_analysis_report: Optional[TenderGapAnalysisReport] = None
        if tender_specification is not None:
            if isinstance(tender_specification, TenderDocument):
                tender_doc_obj = tender_specification
            elif isinstance(tender_specification, str):
                tender_doc_obj = extract_tender_document(text=tender_specification)
            elif isinstance(tender_specification, dict):
                tender_doc_obj = extract_tender_document(
                    text=tender_specification.get("text") or tender_specification.get("raw_text"),
                    title=tender_specification.get("title"),
                    issuer=tender_specification.get("issuer"),
                )

            if tender_doc_obj and (discovered_reqs or candidates):
                tender_analysis_report = analyze_tender_gaps(
                    tender=tender_doc_obj,
                    standard_requirements=discovered_reqs,
                    product_spec=technical_spec_obj,
                )

        # Phase 14: Document Intelligence + Evidence Gap Analysis
        extracted_business_docs: List[BusinessDocument] = []
        evidence_gap_report: Optional[EvidenceGapReport] = None
        if compliance_documents:
            for cdoc in compliance_documents:
                if isinstance(cdoc, BusinessDocument):
                    extracted_business_docs.append(cdoc)
                elif isinstance(cdoc, dict):
                    bdoc = extract_business_document(
                        text=cdoc.get("text") or cdoc.get("raw_text") or "",
                        filename=cdoc.get("filename"),
                        document_id=cdoc.get("document_id"),
                    )
                    extracted_business_docs.append(bdoc)
                elif isinstance(cdoc, str):
                    bdoc = extract_business_document(text=cdoc)
                    extracted_business_docs.append(bdoc)

            eval_reqs = discovered_reqs
            if not eval_reqs and tender_doc_obj:
                eval_reqs = tender_doc_obj.requirements

            if extracted_business_docs and eval_reqs:
                evidence_gap_report = build_evidence_gap_report(
                    requirements=eval_reqs,
                    documents=extracted_business_docs,
                )

        # Phase 15: Applicability Intelligence & Compliance Readiness Synthesis
        applicability_assessments: List[ApplicabilityAssessment] = []
        readiness_report: Optional[ComplianceReadinessReport] = None

        if candidates:
            for cand in candidates[:5]:
                scope_text = None
                if self.knowledge_repo is not None:
                    clauses = self.knowledge_repo.get_standard_clauses(cand.standard_id)
                    for cl in clauses:
                        if cl.clause_number in ("1", "1.1") or "scope" in (cl.clause_title or "").lower():
                            scope_text = cl.clause_text
                            break

                assessment = evaluate_standard_applicability(
                    product=product_context_obj,
                    candidate=cand,
                    scope_text=scope_text,
                )
                applicability_assessments.append(assessment)

            readiness_report = synthesize_compliance_readiness(
                product=product_context_obj,
                applicability_assessments=applicability_assessments,
                technical_analysis=technical_analysis_report,
                tender_analysis=tender_analysis_report,
                evidence_report=evidence_gap_report,
            )


        # ====================================================
        # STEP 3: GENERATION
        # ====================================================

        logger.info(
            "Step 3/3: Generating grounded answer (decision=%s, confidence=%.2f)...",
            confidence.decision.value,
            confidence.score,
        )

        if self.generator is not None:
            generation_result = self.generator.generate(
                query=query,
                results=evidence_items,
                confidence=confidence,
            )
            answer = generation_result.get("answer", "")
            raw_claims = generation_result.get("raw_claims", [])

            # Post-generation grounding validation (Phase 8)
            grounding = GroundingValidator.validate(
                answer=answer,
                evidence_items=evidence_items,
                raw_claims=raw_claims,
            )

            # Safe repair / regeneration bounded to 1 attempt (Section 18)
            if grounding.status == GroundingStatus.UNSUPPORTED and grounding.unsupported_claims_summary:
                logger.warning(
                    "Grounding validation detected unsupported claims: %s. Attempting bounded regeneration.",
                    grounding.unsupported_claims_summary,
                )
                repair_result = self.generator.generate(
                    query=query,
                    results=evidence_items,
                    confidence=confidence,
                    repair_feedback=grounding.unsupported_claims_summary,
                )
                repaired_answer = repair_result.get("answer", "")
                repaired_claims = repair_result.get("raw_claims", [])
                repaired_grounding = GroundingValidator.validate(
                    answer=repaired_answer,
                    evidence_items=evidence_items,
                    raw_claims=repaired_claims,
                )
                if repaired_grounding.status != GroundingStatus.UNSUPPORTED:
                    answer = repaired_answer
                    grounding = repaired_grounding
                else:
                    logger.warning("Repaired generation still contains unsupported claims. Enforcing verification_required.")
                    answer = (
                        f"Verification Required: The generated response contains ungrounded claims that could not be verified "
                        f"against authoritative BIS standard text ({repaired_grounding.reason or 'evidence mismatch'}). "
                        f"Manual regulatory verification is required."
                    )
                    grounding = repaired_grounding
        else:
            if query_context.intent.intent == QueryIntentType.APPLICABILITY_QUERY:
                target_p = product_context_obj.primary_identifier if product_context_obj else 'the specified product'
                if candidate_standards_list:
                    top_c = candidate_standards_list[0]
                    answer = (
                        f"Candidate standard {top_c['standard_number']} ({top_c['mapping_status']}) was identified for {target_p} "
                        f"based on technical/product terminology and supporting standard text. "
                        f"However, legal applicability and mandatory certification requirements require verification "
                        f"against authoritative gazette notifications and QCO schedules."
                    )
                else:
                    answer = (
                        f"Verification Required: No candidate standard was identified for {target_p} in the current corpus. "
                        f"Please verify against authoritative BIS publications."
                    )
            elif query_context.intent.intent == QueryIntentType.STANDARD_DISCOVERY:
                target_p = product_context_obj.primary_identifier if product_context_obj else 'the specified product'
                if candidate_standards_list:
                    c_summaries = [f"{c['standard_number']} ({c['mapping_status']}, score={c['mapping_score']:.2f})" for c in candidate_standards_list[:3]]
                    answer = (
                        f"Candidate Indian Standard(s) identified for {target_p}: {', '.join(c_summaries)}. "
                        f"Please verify scope and clauses against authoritative BIS publications."
                    )
                else:
                    answer = (
                        f"Verification Required: No candidate standards found in the ingested corpus for {target_p}. "
                        f"Please verify against authoritative Indian Standard publications."
                    )
            elif confidence.decision == Decision.VERIFICATION_REQUIRED:
                answer = (
                    f"Verification Required: {confidence.verification_reason or 'Available evidence is insufficient.'} "
                    f"Retrieved {len(reranked_results)} relevant context passage(s)."
                )
            elif confidence.decision == Decision.QUALIFIED_ANSWER:
                answer = (
                    f"Qualified Answer (LLM generation disabled): "
                    f"Retrieved {len(reranked_results)} relevant context passage(s) with moderate confidence. "
                    f"Please review citations for exact requirements."
                )
            else:
                answer = (
                    "LLM generation is disabled (GROQ_API_KEY is not set). "
                    f"Retrieved {len(reranked_results)} relevant context passage(s)."
                )
            generation_result = {
                "answer": answer,
                "model": "retrieval-only",
            }
            grounding = GroundingValidator.validate(
                answer=answer,
                evidence_items=evidence_items,
                raw_claims=[],
            )

        # ====================================================
        # BUILD SOURCES & CITATIONS
        # ====================================================

        sources = (
            self._build_sources(
                reranked_results
            )
        )

        citations = [c.to_dict() for c in grounding.citations]
        claims = [c.to_dict() for c in grounding.claims]

        # ====================================================
        # FINAL ANSWER POLICY (Section 17 & 22)
        # ====================================================

        final_decision = confidence.decision.value
        final_verification_required = confidence.verification_required
        final_verification_reason = confidence.verification_reason

        if not final_verification_required:
            if grounding.status == GroundingStatus.UNSUPPORTED:
                final_decision = Decision.VERIFICATION_REQUIRED.value
                final_verification_required = True
                final_verification_reason = (
                    f"Grounding failure: {grounding.reason} "
                    f"({grounding.unsupported_claims_summary or 'unsupported claims detected'})"
                )
            elif grounding.status == GroundingStatus.PARTIALLY_GROUNDED:
                if final_decision == Decision.ANSWER.value:
                    final_decision = Decision.QUALIFIED_ANSWER.value
            elif grounding.status == GroundingStatus.UNVERIFIABLE:
                if final_decision == Decision.ANSWER.value:
                    final_decision = Decision.QUALIFIED_ANSWER.value

        # Phase 9: Temporal policy enforcement (Section 13 & 18)
        is_currentness_query = (
            getattr(entities, "relative_temporal", None) == "current"
            or getattr(entities, "temporal_intent", None) == "current"
            or bool(re.search(r"\b(?:current|latest|present|active|in\s+force)\b", normalize_query(query), re.IGNORECASE))
        )
        if is_currentness_query and temporal_resolution.requires_verification:
            final_decision = Decision.VERIFICATION_REQUIRED.value
            final_verification_required = True
            if not final_verification_reason:
                final_verification_reason = f"Temporal verification required: {temporal_resolution.reason}"

        # Phase 11: Candidate mapping policy enforcement (Section 20, 21, 30)
        if query_context.intent.intent == QueryIntentType.APPLICABILITY_QUERY:
            final_decision = Decision.VERIFICATION_REQUIRED.value
            final_verification_required = True
            if not final_verification_reason:
                final_verification_reason = (
                    "Candidate standard mapping prepared. Legal applicability and mandatory certification "
                    "require verification against gazette notifications and QCO schedules."
                )
        elif query_context.intent.intent == QueryIntentType.STANDARD_DISCOVERY and not candidate_standards_list:
            final_decision = Decision.VERIFICATION_REQUIRED.value
            final_verification_required = True
            if not final_verification_reason:
                final_verification_reason = (
                    "No candidate standards found in the ingested corpus matching the specified product."
                )

        # ====================================================
        # FINAL RESPONSE
        # ====================================================

        evidence_summary = (
            f"{confidence.supporting_evidence_count} supporting passage(s) evaluated; "
            f"confidence={confidence.level.value} ({confidence.score:.2f}); "
            f"grounding={grounding.status.value} ({grounding.groundedness_score:.2f}); "
            f"temporal={temporal_resolution.status.value}; "
            f"decision={final_decision}."
        )

        response = {
            "query": query,
            "answer": answer,
            "sources": sources,
            "retrieved_chunks": len(retrieved_results),
            "reranked_chunks": len(reranked_results),
            "model": generation_result.get("model"),
            "confidence_score": confidence.score,
            "confidence_level": confidence.level.value,
            "decision": final_decision,
            "query_state": confidence.query_state.value,
            "verification_required": final_verification_required,
            "verification_reason": final_verification_reason,
            "evidence_summary": evidence_summary,
            "confidence_trace": confidence.trace,
            "citations": citations,
            "claims": claims,
            "citation_coverage": grounding.citation_coverage,
            "grounding_status": grounding.status.value,
            "grounding_reason": grounding.reason,
            "groundedness_score": grounding.groundedness_score,
            # Phase 9 Temporal fields
            "temporal_status": temporal_resolution.status.value,
            "temporal_resolution": temporal_resolution.to_dict(),
            "candidate_versions": temporal_resolution.candidate_versions,
            "temporal_conflict": len(temporal_resolution.conflicts) > 0,
            "temporal_verification_required": temporal_resolution.requires_verification,
            "temporal_trace": temporal_trace_obj.to_dict() if temporal_trace_obj else None,
            # Phase 10 Query Intelligence fields
            "intent": query_context.intent.intent.value,
            "intent_confidence": query_context.intent.intent_confidence,
            "query_context": query_context.to_dict(),
            # Phase 11 Product-to-Standard Candidate Mapping fields
            "product_context": product_context_obj.to_dict() if product_context_obj else None,
            "candidate_standards": candidate_standards_list,
            # Phase 12 Technical Specification Analysis fields
            "technical_specification_data": technical_spec_obj.to_dict() if technical_spec_obj else None,
            "technical_analysis": technical_analysis_report.to_dict() if technical_analysis_report else None,
            # Phase 13 Tender Specification Analysis fields
            "tender_analysis": tender_analysis_report.to_dict() if tender_analysis_report else None,
            # Phase 14 Document Intelligence & Evidence Gap fields
            "evidence_gap_report": evidence_gap_report.to_dict() if evidence_gap_report else None,
            # Phase 15 Applicability Intelligence & Compliance Readiness fields
            "compliance_readiness": readiness_report.to_dict() if readiness_report else None,
        }


        logger.info(
            "Pipeline completed successfully."
        )

        return response


# ============================================================
# OPTIONAL SIMPLE INTERACTIVE TEST
# ============================================================

if __name__ == "__main__":

    try:

        pipeline = RAGPipeline()

        print("\n" + "=" * 70)
        print("BIS RAG PIPELINE")
        print("=" * 70)

        print(
            "\nType a question."
        )

        print(
            "Type 'exit' to quit.\n"
        )

        while True:

            user_query = input(
                "Question: "
            ).strip()

            if user_query.lower() in (
                "exit",
                "quit"
            ):

                print(
                    "\nExiting pipeline."
                )

                break

            if not user_query:

                print(
                    "\nPlease enter a question.\n"
                )

                continue

            print("\n")

            print(
                "Processing..."
            )

            print("\n")

            result = (
                pipeline.query(
                    user_query
                )
            )

            print(
                "=" * 70
            )

            print(
                "ANSWER"
            )

            print(
                "=" * 70
            )

            print(
                "\n"
                + result["answer"]
            )

            # ------------------------------------------------
            # SOURCES
            # ------------------------------------------------

            sources = result.get(
                "sources",
                []
            )

            if sources:

                print("\n")

                print(
                    "-" * 70
                )

                print(
                    "SOURCES"
                )

                print(
                    "-" * 70
                )

                for index, source in enumerate(
                    sources,
                    start=1
                ):

                    print(
                        f"\n{index}."
                    )

                    for key, value in (
                        source.items()
                    ):

                        print(
                            f"{key}: {value}"
                        )

            print("\n")

    except KeyboardInterrupt:

        print(
            "\n\nPipeline stopped."
        )

    except Exception as error:

        print("\n" + "=" * 70)
        print("PIPELINE ERROR")
        print("=" * 70)

        logger.exception(
            error
        )
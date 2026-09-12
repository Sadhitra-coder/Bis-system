"""
app/knowledge/service.py

High-level Knowledge Service and Ingestion Builder for BIS Compliance Intelligence.
Coordinates repository persistence, knowledge hierarchy generation, and graph queries.
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

from app.models import DocumentMetadata
from app.knowledge.models import (
    Standard,
    StandardVersion,
    StandardPart,
    Clause,
    Amendment,
    StandardReference,
    StandardStatus,
    ReferenceType,
    ResolutionStatus,
    KnowledgeDiagnostics,
)
from app.knowledge.normalization import (
    normalize_standard_number,
    normalize_clause_number,
    derive_clause_hierarchy,
    derive_standard_id,
    derive_version_id,
    derive_version_key,
    derive_part_id,
    derive_clause_id,
    derive_clause_key,
    derive_amendment_id,
    derive_reference_id,
    establishes_standard_identity,
    is_document_header_section,
)
from app.knowledge.repository import KnowledgeRepository, default_repository
from app.knowledge.validator import validate_knowledge_graph

logger = logging.getLogger(__name__)


class KnowledgeService:
    """Service layer exposing query operations and ingestion mapping for BIS entities."""

    def __init__(self, repository: Optional[KnowledgeRepository] = None, repo: Optional[KnowledgeRepository] = None):
        self.repo = repository or repo or default_repository

    # ========================================================
    # QUERY OPERATIONS
    # ========================================================

    def get_standard(self, standard_id: str) -> Optional[Standard]:
        return self.repo.get_standard(standard_id)

    def get_standard_by_number(self, standard_number: str) -> Optional[Standard]:
        norm = normalize_standard_number(standard_number)
        return self.repo.get_standard_by_number(norm)

    def get_standard_versions(self, standard_id: str) -> List[StandardVersion]:
        return self.repo.get_standard_versions(standard_id)

    def get_standard_clauses(self, standard_id: str, version_id: Optional[str] = None) -> List[Clause]:
        return self.repo.get_standard_clauses(standard_id, version_id)

    def get_clause(self, clause_id: str) -> Optional[Clause]:
        return self.repo.get_clause(clause_id)

    def get_amendments(self, standard_id: str) -> List[Amendment]:
        return self.repo.get_amendments(standard_id)

    def get_standard_references(self, standard_id: str) -> List[StandardReference]:
        return self.repo.get_standard_references(standard_id)

    # ========================================================
    # INGESTION MAPPING
    # ========================================================

    def build_knowledge_from_ingestion(
        self,
        doc_metadata: DocumentMetadata,
        chunks: List[Dict[str, Any]]
    ) -> KnowledgeDiagnostics:
        """
        Map ingested document and its semantic chunks into the BIS Knowledge Model.

        Distinction:
        - Indian Standards produce: Standard -> Version -> (Part) -> Clauses -> References
        - Non-standards (Gazette orders, drafts without IS numbers) are NOT forced into Standard entities.
        """
        diagnostics = KnowledgeDiagnostics()
        now = time.time()

        # Check if this document represents an Indian Standard
        raw_std_num = doc_metadata.standard_number
        doc_type = doc_metadata.document_type

        # A document that merely CITES an IS number is not that standard.
        # Product manuals, Gazette orders and tenders routinely quote IS
        # numbers, and the conservative extractor records the number it
        # finds in the header region either way. Promoting that citation
        # to identity would fabricate a Standard entity and collapse two
        # unrelated documents into one logical standard.
        #
        # The decision is delegated to classify_standard_relation() so the
        # knowledge model and the vector index cannot disagree about it.
        if not establishes_standard_identity(doc_type, raw_std_num):
            if raw_std_num:
                logger.info(
                    "Document %s cites standard %r but its document_type=%r does not "
                    "establish standard identity; recording as a reference only.",
                    doc_metadata.document_id, raw_std_num, doc_type,
                )
            else:
                logger.info(
                    "Document %s carries no standard number; skipping Standard entity generation.",
                    doc_metadata.document_id,
                )
            return diagnostics

        norm_std_num = normalize_standard_number(raw_std_num)
        standard_id = derive_standard_id(norm_std_num)

        # 1. Standard Entity
        existing_std = self.repo.get_standard(standard_id)
        standard = Standard(
            standard_id=standard_id,
            standard_number=norm_std_num,
            standard_title=doc_metadata.standard_title or doc_metadata.document_title,
            authority=doc_metadata.authority or "BIS",
            standard_year=doc_metadata.standard_year,
            part_number=doc_metadata.part_number,
            document_id=doc_metadata.document_id,
            edition_or_version=doc_metadata.edition_or_version,
            publication_date=doc_metadata.publication_date,
            effective_date=doc_metadata.effective_date,
            withdrawal_date=doc_metadata.withdrawal_date,
            status=StandardStatus.UNKNOWN,
            source_url=doc_metadata.source_url,
            is_current=doc_metadata.is_current,
            created_at=existing_std.created_at if existing_std else now,
            updated_at=now,
        )
        self.repo.save_standard(standard)
        if not existing_std:
            diagnostics.standards_created += 1

        # 2. Version / Edition Entity (historical versions are never overwritten)
        version_id = derive_version_id(
            standard_id,
            derive_version_key(doc_metadata.edition_or_version, doc_metadata.standard_year),
        )
        version = StandardVersion(
            version_id=version_id,
            standard_id=standard_id,
            edition=doc_metadata.edition_or_version,
            standard_year=doc_metadata.standard_year,
            publication_date=doc_metadata.publication_date,
            effective_date=doc_metadata.effective_date,
            withdrawal_date=doc_metadata.withdrawal_date,
            status=StandardStatus.UNKNOWN,
            document_id=doc_metadata.document_id,
            source_url=doc_metadata.source_url,
            created_at=now,
        )
        self.repo.save_version(version)
        diagnostics.versions_created += 1

        # 3. Part Entity (if multi-part standard)
        part_id = None
        if doc_metadata.part_number:
            part_id = derive_part_id(standard_id, doc_metadata.part_number)
            part = StandardPart(
                part_id=part_id,
                standard_id=standard_id,
                part_number=doc_metadata.part_number,
                part_title=doc_metadata.standard_title,
                document_id=doc_metadata.document_id,
                source_url=doc_metadata.source_url,
                created_at=now,
            )
            self.repo.save_part(part)
            diagnostics.parts_created += 1

        # 4. Amendment Entity (if explicitly an amendment or contains amendment metadata)
        if doc_metadata.amendment_number:
            amendment_id = derive_amendment_id(standard_id, doc_metadata.amendment_number)
            amendment = Amendment(
                amendment_id=amendment_id,
                standard_id=standard_id,
                version_id=version_id,
                amendment_number=doc_metadata.amendment_number,
                title=f"Amendment No. {doc_metadata.amendment_number}",
                publication_date=doc_metadata.publication_date,
                effective_date=doc_metadata.effective_date,
                source_document_id=doc_metadata.document_id,
                source_url=doc_metadata.source_url,
                status=StandardStatus.UNKNOWN,
                created_at=now,
            )
            self.repo.save_amendment(amendment)
            diagnostics.amendments_created += 1

        # 5. Build Clause Hierarchy from chunks
        clauses_by_number: Dict[str, Clause] = {}
        clauses_list: List[Clause] = []
        clause_order: List[str] = []

        for chunk in chunks:
            meta = chunk.get("metadata") or chunk
            sec_heading = chunk.get("section") or meta.get("section") or "Section"
            heading_ctx = chunk.get("heading_context") or meta.get("heading_context") or []

            # The clause key is derived by ONE canonical function so that the
            # vector-index serializer produces byte-identical clause_ids from
            # the same chunk. Divergence here breaks the knowledge <-> retrieval
            # join while every unit test still passes.
            clause_num, h_path = derive_clause_key(
                section=sec_heading,
                heading_context=heading_ctx,
                clause_number_hint=chunk.get("clause_id") or meta.get("clause_id"),
            )

            # HEADER-IS-NOT-A-CLAUSE GUARD:
            # Skip chunks whose section heading matches the standard number pattern
            # (e.g. "IS 3055 : 2024", "IS 3055") — these are document headers,
            # not normative clauses. They belong to StandardVersion metadata, not Clause.
            if is_document_header_section(sec_heading, clause_num):
                logger.debug("Skipping header chunk as not a normative clause: %r", sec_heading)
                continue

            # version-scoped clause_id prevents cross-version collision
            clause_id = derive_clause_id(standard_id, version_id, clause_num, h_path)
            chunk_id = chunk.get("chunk_id") or meta.get("chunk_id")

            # Determine level and parent clause
            level, parent_num = derive_clause_hierarchy(clause_num, heading_level=len(heading_ctx) + 1 if heading_ctx else 1)


            if clause_id not in clauses_by_number:
                cls_obj = Clause(
                    clause_id=clause_id,
                    standard_id=standard_id,
                    version_id=version_id,
                    part_id=part_id,
                    parent_clause_id=None,  # will link after gathering
                    clause_number=clause_num,
                    clause_title=sec_heading,
                    level=level,
                    document_id=doc_metadata.document_id,
                    page_start=meta.get("page_start") or meta.get("page_number") or chunk.get("page_start"),
                    page_end=meta.get("page_end") or meta.get("page_number") or chunk.get("page_end"),
                    heading_path=h_path,
                    source_chunk_ids=[chunk_id] if chunk_id else [],
                    created_at=now,
                )
                clauses_by_number[clause_id] = cls_obj
                clause_order.append(clause_id)
            else:
                # Add additional chunk to existing clause
                cls_obj = clauses_by_number[clause_id]
                if chunk_id and chunk_id not in cls_obj.source_chunk_ids:
                    cls_obj.source_chunk_ids.append(chunk_id)
                p_end = meta.get("page_end") or meta.get("page_number") or chunk.get("page_end")
                if p_end:
                    cls_obj.page_end = max(cls_obj.page_end or p_end, p_end)

        # Resolve parent_clause_id links using clause numbers and hierarchical paths
        for cid in clause_order:
            cls_obj = clauses_by_number[cid]
            if cls_obj.clause_number and "." in cls_obj.clause_number:
                parent_num = ".".join(cls_obj.clause_number.split(".")[:-1])
                # Find matching parent clause
                for cand in clauses_by_number.values():
                    if cand.clause_number == parent_num:
                        cls_obj.parent_clause_id = cand.clause_id
                        break
            clauses_list.append(cls_obj)
            self.repo.save_clause(cls_obj)
            diagnostics.clauses_created += 1

        # 6. Extract Standard References from chunk text
        references_list: List[StandardReference] = []
        is_ref_pattern = re.compile(r'\bIS(?:\s+No\.?)?\s*(\d+(?:\s*-\s*\d+)?)\b', re.IGNORECASE)

        for chunk in chunks:
            content = chunk.get("content", "")
            meta = chunk.get("metadata") or chunk
            matches = is_ref_pattern.findall(content)
            for m in set(matches):
                target_norm = f"IS {m.strip()}"
                if target_norm == norm_std_num:
                    continue  # Self-reference ignored

                # Check if target standard already exists in knowledge store
                target_std = self.repo.get_standard_by_number(target_norm)
                ref_id = derive_reference_id(standard_id, target_norm, meta.get("clause_id"))
                
                ref = StandardReference(
                    relationship_id=ref_id,
                    source_standard_id=standard_id,
                    target_standard_number=target_norm,
                    target_standard_id=target_std.standard_id if target_std else None,
                    relationship_type=ReferenceType.REFERENCES,
                    source_document_id=doc_metadata.document_id,
                    source_clause_id=meta.get("clause_id"),
                    source_chunk_id=chunk.get("chunk_id"),
                    resolution_status=ResolutionStatus.RESOLVED if target_std else ResolutionStatus.UNRESOLVED,
                    confidence=1.0,
                    created_at=now,
                )
                self.repo.save_reference(ref)
                references_list.append(ref)
                diagnostics.references_created += 1
                if not target_std:
                    diagnostics.unresolved_references += 1

        # 7. Run Knowledge Graph Integrity Validation
        warnings, errors = validate_knowledge_graph(
            standards=[standard],
            versions=[version],
            parts=[part] if part_id else [],
            clauses=clauses_list,
            amendments=[amendment] if doc_metadata.amendment_number else [],
            references=references_list,
        )
        diagnostics.validation_warnings = warnings
        diagnostics.validation_errors = errors

        logger.info(
            "Knowledge building complete for %s: %d clauses, %d references (%d unresolved), %d warnings, %d errors",
            norm_std_num,
            diagnostics.clauses_created,
            diagnostics.references_created,
            diagnostics.unresolved_references,
            len(warnings),
            len(errors)
        )

        return diagnostics


default_knowledge_service = KnowledgeService()

"""
app/knowledge/validator.py

Integrity validators for the BIS Knowledge Model:
- Standard
- StandardVersion
- StandardPart
- Clause hierarchy & parent relationships (version-scoped)
- Amendment relationships
- StandardReference integrity

Reports issues as structured warnings and errors without fabricating or silently altering data.
"""

from typing import List, Tuple
from app.knowledge.models import (
    Standard,
    StandardVersion,
    StandardPart,
    Clause,
    Amendment,
    StandardReference,
)


def validate_knowledge_graph(
    standards: List[Standard],
    versions: List[StandardVersion],
    parts: List[StandardPart],
    clauses: List[Clause],
    amendments: List[Amendment],
    references: List[StandardReference],
) -> Tuple[List[str], List[str]]:
    """
    Validate relationships and hierarchy across knowledge objects.

    Checks include:
    - Duplicate IDs within each entity type
    - Versions pointing to nonexistent standards
    - Clauses pointing to nonexistent versions (error) or standards (error)
    - Duplicate clause_id within one version (cross-version same clause_id is OK
      only if derive_clause_id was called correctly with version_id; if two clauses
      in the same version share a clause_id that is an ID derivation bug)
    - Parent clause from a different version (warning)
    - Inverted clause hierarchy levels
    - Malformed reference targets

    Returns:
        (errors, warnings)
    """
    warnings: List[str] = []
    errors: List[str] = []

    # Build lookup sets
    standard_ids = set()
    for std in standards:
        if std.standard_id in standard_ids:
            errors.append(f"duplicate_standard_id:{std.standard_id}")
        standard_ids.add(std.standard_id)

    # Validate versions have parent standard
    version_ids = set()
    version_to_standard: dict = {}
    for ver in versions:
        if ver.version_id in version_ids:
            errors.append(f"duplicate_version_id:{ver.version_id}")
        version_ids.add(ver.version_id)
        version_to_standard[ver.version_id] = ver.standard_id
        if ver.standard_id not in standard_ids:
            errors.append(f"version_missing_parent_standard:{ver.version_id}->{ver.standard_id}")

    # Validate parts have parent standard
    part_ids = set()
    for part in parts:
        if part.part_id in part_ids:
            errors.append(f"duplicate_part_id:{part.part_id}")
        part_ids.add(part.part_id)
        if part.standard_id not in standard_ids:
            errors.append(f"part_missing_parent_standard:{part.part_id}->{part.standard_id}")

    # Validate amendments have parent standard
    amendment_ids = set()
    for amd in amendments:
        if amd.amendment_id in amendment_ids:
            errors.append(f"duplicate_amendment_id:{amd.amendment_id}")
        amendment_ids.add(amd.amendment_id)
        if amd.standard_id not in standard_ids:
            errors.append(f"amendment_missing_parent_standard:{amd.amendment_id}->{amd.standard_id}")

    # Validate clauses:
    # - clause_id uniqueness (globally — version-scoped IDs prevent false positives here)
    # - version_id points to a known version (error if unknown)
    # - standard_id points to a known standard (error if unknown)
    # - parent_clause_id within same version (warning if cross-version)
    # - detect duplicate (clause_id, version_id) pairs — would indicate ID derivation bug
    clause_ids = set()
    clause_by_id: dict = {}
    version_clause_numbers: dict = {}  # (version_id, clause_number) -> clause_id

    for cls in clauses:
        if cls.clause_id in clause_ids:
            errors.append(f"duplicate_clause_id:{cls.clause_id}")
        clause_ids.add(cls.clause_id)
        clause_by_id[cls.clause_id] = cls

        if cls.level <= 0:
            errors.append(f"invalid_clause_level:{cls.clause_id}:level={cls.level}")

        if not cls.source_chunk_ids:
            warnings.append(f"clause_missing_source_chunks:{cls.clause_id}")

        # Clause must reference a known standard
        if cls.standard_id not in standard_ids:
            errors.append(f"clause_unknown_standard:{cls.clause_id}->{cls.standard_id}")

        # Clause must reference a known version (error if set but not known)
        if cls.version_id and cls.version_id not in version_ids:
            errors.append(f"clause_unknown_version:{cls.clause_id}->{cls.version_id}")

        # Clause version must belong to clause's standard
        if cls.version_id and cls.version_id in version_to_standard:
            if version_to_standard[cls.version_id] != cls.standard_id:
                errors.append(
                    f"clause_version_standard_mismatch:{cls.clause_id}:"
                    f"version={cls.version_id}_belongs_to_{version_to_standard[cls.version_id]}"
                    f"_not_{cls.standard_id}"
                )

        # Track (version_id, clause_number) uniqueness within version
        if cls.clause_number and cls.version_id:
            key = (cls.version_id, cls.clause_number)
            if key in version_clause_numbers:
                errors.append(
                    f"duplicate_clause_number_within_version:"
                    f"version={cls.version_id}:clause={cls.clause_number}:"
                    f"ids={version_clause_numbers[key]},{cls.clause_id}"
                )
            else:
                version_clause_numbers[key] = cls.clause_id

    # Check parent-child relationships
    for cls in clauses:
        if cls.parent_clause_id:
            parent = clause_by_id.get(cls.parent_clause_id)
            if not parent:
                warnings.append(f"orphan_clause:{cls.clause_id}:parent_not_found={cls.parent_clause_id}")
            else:
                # Parent clause from different version is a warning (not error —
                # some cross-edition references may be intentional)
                if parent.version_id and cls.version_id and parent.version_id != cls.version_id:
                    warnings.append(
                        f"parent_clause_different_version:{cls.clause_id}(v={cls.version_id})"
                        f"->parent {parent.clause_id}(v={parent.version_id})"
                    )
                # Inverted level
                if parent.level >= cls.level:
                    warnings.append(
                        f"inverted_clause_hierarchy:{cls.clause_id}(lvl={cls.level})_under_"
                        f"{parent.clause_id}(lvl={parent.level})"
                    )

    # Validate references
    ref_ids = set()
    for ref in references:
        if ref.relationship_id in ref_ids:
            errors.append(f"duplicate_reference_id:{ref.relationship_id}")
        ref_ids.add(ref.relationship_id)

        if not ref.target_standard_number or not ref.target_standard_number.strip():
            errors.append(f"malformed_reference_target:{ref.relationship_id}")

        if ref.source_standard_id not in standard_ids:
            warnings.append(f"reference_unknown_source_standard:{ref.relationship_id}->{ref.source_standard_id}")

    return errors, warnings

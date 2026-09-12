"""
app/knowledge/repository.py

Relational persistence layer for the BIS Knowledge Model using Python's standard sqlite3.
Zero external services required; fully ACID-compliant, portable, and queryable.
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import DATA_DIR
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
)

DEFAULT_DB_PATH = DATA_DIR / "knowledge" / "bis_knowledge.db"


class KnowledgeRepository:
    """Thread-safe SQLite repository for BIS Knowledge entities."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._lock = threading.Lock()
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        else:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.executescript("""
            CREATE TABLE IF NOT EXISTS standards (
                standard_id TEXT PRIMARY KEY,
                standard_number TEXT NOT NULL,
                standard_title TEXT,
                authority TEXT,
                standard_year INTEGER,
                part_number TEXT,
                document_id TEXT NOT NULL,
                edition_or_version TEXT,
                publication_date TEXT,
                effective_date TEXT,
                withdrawal_date TEXT,
                status TEXT,
                source_url TEXT,
                is_current INTEGER,
                created_at REAL,
                updated_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_standards_number ON standards(standard_number);

            CREATE TABLE IF NOT EXISTS standard_versions (
                version_id TEXT PRIMARY KEY,
                standard_id TEXT NOT NULL,
                edition TEXT,
                standard_year INTEGER,
                publication_date TEXT,
                effective_date TEXT,
                withdrawal_date TEXT,
                status TEXT,
                document_id TEXT NOT NULL,
                source_url TEXT,
                created_at REAL,
                FOREIGN KEY(standard_id) REFERENCES standards(standard_id)
            );

            CREATE INDEX IF NOT EXISTS idx_versions_std ON standard_versions(standard_id);

            CREATE TABLE IF NOT EXISTS standard_parts (
                part_id TEXT PRIMARY KEY,
                standard_id TEXT NOT NULL,
                part_number TEXT NOT NULL,
                part_title TEXT,
                document_id TEXT NOT NULL,
                source_url TEXT,
                created_at REAL,
                FOREIGN KEY(standard_id) REFERENCES standards(standard_id)
            );

            CREATE INDEX IF NOT EXISTS idx_parts_std ON standard_parts(standard_id);

            CREATE TABLE IF NOT EXISTS clauses (
                clause_id TEXT PRIMARY KEY,
                standard_id TEXT NOT NULL,
                version_id TEXT,
                part_id TEXT,
                parent_clause_id TEXT,
                clause_number TEXT,
                clause_title TEXT NOT NULL,
                level INTEGER NOT NULL,
                document_id TEXT NOT NULL,
                page_start INTEGER,
                page_end INTEGER,
                heading_path TEXT,
                source_chunk_ids TEXT,
                created_at REAL,
                FOREIGN KEY(standard_id) REFERENCES standards(standard_id)
            );

            CREATE INDEX IF NOT EXISTS idx_clauses_std ON clauses(standard_id);
            CREATE INDEX IF NOT EXISTS idx_clauses_parent ON clauses(parent_clause_id);
            CREATE INDEX IF NOT EXISTS idx_clauses_num ON clauses(clause_number);

            CREATE TABLE IF NOT EXISTS amendments (
                amendment_id TEXT PRIMARY KEY,
                standard_id TEXT NOT NULL,
                version_id TEXT,
                amendment_number TEXT NOT NULL,
                title TEXT,
                publication_date TEXT,
                effective_date TEXT,
                source_document_id TEXT NOT NULL,
                source_url TEXT,
                status TEXT,
                created_at REAL,
                FOREIGN KEY(standard_id) REFERENCES standards(standard_id)
            );

            CREATE INDEX IF NOT EXISTS idx_amendments_std ON amendments(standard_id);

            CREATE TABLE IF NOT EXISTS standard_references (
                relationship_id TEXT PRIMARY KEY,
                source_standard_id TEXT NOT NULL,
                target_standard_number TEXT NOT NULL,
                target_standard_id TEXT,
                relationship_type TEXT NOT NULL,
                source_document_id TEXT NOT NULL,
                source_clause_id TEXT,
                source_chunk_id TEXT,
                resolution_status TEXT NOT NULL,
                confidence REAL,
                created_at REAL,
                FOREIGN KEY(source_standard_id) REFERENCES standards(standard_id)
            );

            CREATE INDEX IF NOT EXISTS idx_refs_source ON standard_references(source_standard_id);
            CREATE INDEX IF NOT EXISTS idx_refs_target ON standard_references(target_standard_number);

            CREATE TABLE IF NOT EXISTS temporal_relationships (
                relationship_id TEXT PRIMARY KEY,
                source_entity_id TEXT NOT NULL,
                target_entity_id TEXT NOT NULL,
                relationship_type TEXT NOT NULL,
                source_document_id TEXT NOT NULL,
                source_chunk_ids TEXT,
                source_url TEXT,
                effective_date TEXT,
                publication_date TEXT,
                confidence REAL,
                resolution_status TEXT NOT NULL,
                statement_text TEXT,
                created_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_temp_source ON temporal_relationships(source_entity_id);
            CREATE INDEX IF NOT EXISTS idx_temp_target ON temporal_relationships(target_entity_id);
            """)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def save_standard(self, std: Standard) -> None:
        with self._lock:
            self._conn.execute("""
            INSERT INTO standards (
                standard_id, standard_number, standard_title, authority,
                standard_year, part_number, document_id, edition_or_version,
                publication_date, effective_date, withdrawal_date, status,
                source_url, is_current, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(standard_id) DO UPDATE SET
                standard_title=excluded.standard_title,
                updated_at=excluded.updated_at,
                edition_or_version=COALESCE(excluded.edition_or_version, standards.edition_or_version)
            """, (
                std.standard_id, std.standard_number, std.standard_title, std.authority,
                std.standard_year, std.part_number, std.document_id, std.edition_or_version,
                std.publication_date, std.effective_date, std.withdrawal_date,
                std.status.value, std.source_url, 1 if std.is_current else (0 if std.is_current is False else None),
                std.created_at, std.updated_at
            ))
            self._conn.commit()

    def save_version(self, ver: StandardVersion) -> None:
        with self._lock:
            self._conn.execute("""
            INSERT OR REPLACE INTO standard_versions (
                version_id, standard_id, edition, standard_year,
                publication_date, effective_date, withdrawal_date, status,
                document_id, source_url, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ver.version_id, ver.standard_id, ver.edition, ver.standard_year,
                ver.publication_date, ver.effective_date, ver.withdrawal_date,
                ver.status.value, ver.document_id, ver.source_url, ver.created_at
            ))
            self._conn.commit()

    def save_part(self, part: StandardPart) -> None:
        with self._lock:
            self._conn.execute("""
            INSERT OR REPLACE INTO standard_parts (
                part_id, standard_id, part_number, part_title,
                document_id, source_url, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                part.part_id, part.standard_id, part.part_number, part.part_title,
                part.document_id, part.source_url, part.created_at
            ))
            self._conn.commit()

    def save_clause(self, cls: Clause) -> None:
        with self._lock:
            self._conn.execute("""
            INSERT OR REPLACE INTO clauses (
                clause_id, standard_id, version_id, part_id,
                parent_clause_id, clause_number, clause_title, level,
                document_id, page_start, page_end, heading_path,
                source_chunk_ids, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cls.clause_id, cls.standard_id, cls.version_id, cls.part_id,
                cls.parent_clause_id, cls.clause_number, cls.clause_title, cls.level,
                cls.document_id, cls.page_start, cls.page_end, cls.heading_path,
                json.dumps(cls.source_chunk_ids), cls.created_at
            ))
            self._conn.commit()

    def save_amendment(self, amd: Amendment) -> None:
        with self._lock:
            self._conn.execute("""
            INSERT OR REPLACE INTO amendments (
                amendment_id, standard_id, version_id, amendment_number,
                title, publication_date, effective_date, source_document_id,
                source_url, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                amd.amendment_id, amd.standard_id, amd.version_id, amd.amendment_number,
                amd.title, amd.publication_date, amd.effective_date, amd.source_document_id,
                amd.source_url, amd.status.value, amd.created_at
            ))
            self._conn.commit()

    def save_standard_reference(self, ref: StandardReference) -> None:
        with self._lock:
            self._conn.execute("""
            INSERT OR REPLACE INTO standard_references (
                relationship_id, source_standard_id, target_standard_number,
                target_standard_id, relationship_type, source_document_id,
                source_clause_id, source_chunk_id, resolution_status,
                confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ref.relationship_id, ref.source_standard_id, ref.target_standard_number,
                ref.target_standard_id, ref.relationship_type.value, ref.source_document_id,
                ref.source_clause_id, ref.source_chunk_id, ref.resolution_status.value,
                ref.confidence, ref.created_at
            ))
            self._conn.commit()

    save_reference = save_standard_reference

    def get_standard(self, standard_id: str) -> Optional[Standard]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM standards WHERE standard_id = ?", (standard_id,)).fetchone()
            if not row:
                return None
            return self._row_to_standard(row)

    def get_standard_by_number(self, standard_number: str) -> Optional[Standard]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM standards WHERE standard_number = ?", (standard_number,)).fetchone()
            if not row:
                return None
            return self._row_to_standard(row)

    def list_standards(self) -> List[Standard]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM standards ORDER BY standard_number").fetchall()
            return [self._row_to_standard(r) for r in rows]


    def get_standard_versions(self, standard_id: str) -> List[StandardVersion]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM standard_versions WHERE standard_id = ? ORDER BY created_at", (standard_id,)).fetchall()
            return [self._row_to_version(r) for r in rows]

    def get_version(self, version_id: str) -> Optional[StandardVersion]:
        """
        Direct lookup of one StandardVersion by its ID.

        The knowledge <-> retrieval join needs this: a chunk carries a
        version_id and must answer "which StandardVersion do I belong to?"
        without scanning every version of the standard.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM standard_versions WHERE version_id = ?", (version_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_version(row)

    def _row_to_version(self, row: sqlite3.Row) -> StandardVersion:
        return StandardVersion(
            version_id=row["version_id"],
            standard_id=row["standard_id"],
            edition=row["edition"],
            standard_year=row["standard_year"],
            publication_date=row["publication_date"],
            effective_date=row["effective_date"],
            withdrawal_date=row["withdrawal_date"],
            status=StandardStatus(row["status"]) if row["status"] else StandardStatus.UNKNOWN,
            document_id=row["document_id"],
            source_url=row["source_url"],
            created_at=row["created_at"],
        )

    def get_standard_clauses(self, standard_id: str, version_id: Optional[str] = None) -> List[Clause]:
        with self._lock:
            if version_id:
                rows = self._conn.execute("SELECT * FROM clauses WHERE standard_id = ? AND version_id = ? ORDER BY rowid", (standard_id, version_id)).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM clauses WHERE standard_id = ? ORDER BY rowid", (standard_id,)).fetchall()
            return [self._row_to_clause(r) for r in rows]

    def get_clause(self, clause_id: str) -> Optional[Clause]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM clauses WHERE clause_id = ?", (clause_id,)).fetchone()
            if not row:
                return None
            return self._row_to_clause(row)

    def get_amendments(self, standard_id: str) -> List[Amendment]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM amendments WHERE standard_id = ? ORDER BY created_at", (standard_id,)).fetchall()
            return [
                Amendment(
                    amendment_id=r["amendment_id"],
                    standard_id=r["standard_id"],
                    version_id=r["version_id"],
                    amendment_number=r["amendment_number"],
                    title=r["title"],
                    publication_date=r["publication_date"],
                    effective_date=r["effective_date"],
                    source_document_id=r["source_document_id"],
                    source_url=r["source_url"],
                    status=StandardStatus(r["status"]) if r["status"] else StandardStatus.UNKNOWN,
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    def get_standard_references(self, standard_id: str) -> List[StandardReference]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM standard_references WHERE source_standard_id = ?", (standard_id,)).fetchall()
            return [
                StandardReference(
                    relationship_id=r["relationship_id"],
                    source_standard_id=r["source_standard_id"],
                    target_standard_number=r["target_standard_number"],
                    target_standard_id=r["target_standard_id"],
                    relationship_type=ReferenceType(r["relationship_type"]),
                    source_document_id=r["source_document_id"],
                    source_clause_id=r["source_clause_id"],
                    source_chunk_id=r["source_chunk_id"],
                    resolution_status=ResolutionStatus(r["resolution_status"]),
                    confidence=r["confidence"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    def _row_to_standard(self, row: sqlite3.Row) -> Standard:
        is_cur = None
        if row["is_current"] is not None:
            is_cur = bool(row["is_current"])
        return Standard(
            standard_id=row["standard_id"],
            standard_number=row["standard_number"],
            standard_title=row["standard_title"],
            authority=row["authority"],
            standard_year=row["standard_year"],
            part_number=row["part_number"],
            document_id=row["document_id"],
            edition_or_version=row["edition_or_version"],
            publication_date=row["publication_date"],
            effective_date=row["effective_date"],
            withdrawal_date=row["withdrawal_date"],
            status=StandardStatus(row["status"]) if row["status"] else StandardStatus.UNKNOWN,
            source_url=row["source_url"],
            is_current=is_cur,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_clause(self, row: sqlite3.Row) -> Clause:
        return Clause(
            clause_id=row["clause_id"],
            standard_id=row["standard_id"],
            version_id=row["version_id"],
            part_id=row["part_id"],
            parent_clause_id=row["parent_clause_id"],
            clause_number=row["clause_number"],
            clause_title=row["clause_title"],
            level=row["level"],
            document_id=row["document_id"],
            page_start=row["page_start"],
            page_end=row["page_end"],
            heading_path=row["heading_path"],
            source_chunk_ids=json.loads(row["source_chunk_ids"]) if row["source_chunk_ids"] else [],
            created_at=row["created_at"],
        )

    def _row_to_temporal_relationship(self, row: sqlite3.Row) -> Any:
        from app.temporal.models import TemporalRelationship, TemporalRelationshipType
        return TemporalRelationship(
            relationship_id=row["relationship_id"],
            source_entity_id=row["source_entity_id"],
            target_entity_id=row["target_entity_id"],
            relationship_type=TemporalRelationshipType(row["relationship_type"]),
            source_document_id=row["source_document_id"],
            source_chunk_ids=json.loads(row["source_chunk_ids"]) if row["source_chunk_ids"] else [],
            source_url=row["source_url"],
            effective_date=row["effective_date"],
            publication_date=row["publication_date"],
            confidence=row["confidence"],
            resolution_status=row["resolution_status"],
            statement_text=row["statement_text"],
            created_at=row["created_at"],
        )

    def save_temporal_relationship(self, rel: Any) -> None:
        with self._lock:
            self._conn.execute("""
            INSERT OR REPLACE INTO temporal_relationships (
                relationship_id, source_entity_id, target_entity_id,
                relationship_type, source_document_id, source_chunk_ids,
                source_url, effective_date, publication_date, confidence,
                resolution_status, statement_text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                rel.relationship_id, rel.source_entity_id, rel.target_entity_id,
                rel.relationship_type.value if hasattr(rel.relationship_type, "value") else str(rel.relationship_type),
                rel.source_document_id, json.dumps(rel.source_chunk_ids),
                rel.source_url, rel.effective_date, rel.publication_date,
                rel.confidence, rel.resolution_status, rel.statement_text, rel.created_at,
            ))
            self._conn.commit()

    def get_temporal_relationships(self, entity_id: str) -> List[Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM temporal_relationships WHERE source_entity_id = ? OR target_entity_id = ? ORDER BY created_at",
                (entity_id, entity_id),
            ).fetchall()
            return [self._row_to_temporal_relationship(r) for r in rows]

    # Aliases
    get_standard_amendments = get_amendments
    get_references_for_standard = get_standard_references


default_repository = KnowledgeRepository()


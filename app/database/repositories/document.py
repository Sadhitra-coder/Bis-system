from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database.models import Document


class DocumentRepository:

    def __init__(self, db: Session):
        self.db = db

    # ============================================================
    # BASIC DOCUMENT OPERATIONS
    # ============================================================

    def get_by_id(
        self,
        document_id: int,
    ) -> Optional[Document]:

        return (
            self.db.query(Document)
            .filter(Document.id == document_id)
            .first()
        )

    def get_by_url(
        self,
        canonical_url: str,
    ) -> Optional[Document]:

        return (
            self.db.query(Document)
            .filter(
                Document.canonical_url == canonical_url
            )
            .first()
        )

    # ============================================================
    # GENERIC SEARCH
    # ============================================================

    def search(
        self,
        query: str | None = None,
        document_type: str | None = None,
        category: str | None = None,
        subcategory: str | None = None,
        language: str | None = None,
        limit: int = 10,
    ) -> list[Document]:

        q = self.db.query(Document)

        if document_type:
            q = q.filter(
                Document.document_type.ilike(
                    document_type
                )
            )

        if category:
            q = q.filter(
                Document.category.ilike(
                    category
                )
            )

        if subcategory:
            q = q.filter(
                Document.subcategory.ilike(
                    subcategory
                )
            )

        if language:
            q = q.filter(
                Document.language.ilike(
                    language
                )
            )

        if query:

            query = query.strip()

            q = q.filter(
                or_(
                    Document.title.ilike(
                        f"%{query}%"
                    ),
                    Document.description.ilike(
                        f"%{query}%"
                    ),
                    Document.keywords.ilike(
                        f"%{query}%"
                    ),
                    Document.standard_number.ilike(
                        f"%{query}%"
                    ),
                    Document.document_url.ilike(
                        f"%{query}%"
                    ),
                )
            )

        return q.limit(limit).all()

    # ============================================================
    # STANDARD NUMBER NORMALIZATION
    # ============================================================

    @staticmethod
    def normalize_standard_number(
        value: str | None,
    ) -> str | None:

        if not value:
            return None

        value = value.strip().upper()

        # Normalize whitespace.
        value = re.sub(
            r"\s+",
            " ",
            value,
        )

        # IS-456 -> IS 456
        # IS/456 -> IS 456
        value = re.sub(
            r"^IS\s*[-/]\s*",
            "IS ",
            value,
        )

        # IS456 -> IS 456
        value = re.sub(
            r"^IS(?=\d)",
            "IS ",
            value,
        )

        # Normalize year separators.
        value = re.sub(
            r"\s*[-/]\s*",
            ":",
            value,
        )

        value = re.sub(
            r"\s*:\s*",
            ":",
            value,
        )

        match = re.fullmatch(
            r"IS\s+(\d{2,6})(?::(\d{4}))?",
            value,
        )

        if not match:
            return None

        number = match.group(1)
        year = match.group(2)

        if year:
            return f"IS {number}:{year}"

        return f"IS {number}"

    # ============================================================
    # EXACT STANDARD MATCH IN TITLE
    # ============================================================

    @staticmethod
    def title_contains_exact_standard(
        title: str | None,
        standard_number: str,
    ) -> bool:

        if not title:
            return False

        normalized = (
            DocumentRepository
            .normalize_standard_number(
                standard_number
            )
        )

        if not normalized:
            return False

        match = re.fullmatch(
            r"IS\s+(\d{2,6})(?::(\d{4}))?",
            normalized,
        )

        if not match:
            return False

        number = match.group(1)
        year = match.group(2)

        # --------------------------------------------------------
        # IMPORTANT:
        #
        # The boundaries prevent:
        #
        # IS 456
        #
        # from matching:
        #
        # IS 4566
        # IS 45660
        # IS 4561
        #
        # --------------------------------------------------------

        if year:

            pattern = (
                rf"(?<![A-Z0-9])"
                rf"IS\s*[-/]?\s*"
                rf"{re.escape(number)}"
                rf"\s*[:/-]\s*"
                rf"{re.escape(year)}"
                rf"(?!\d)"
            )

        else:

            pattern = (
                rf"(?<![A-Z0-9])"
                rf"IS\s*[-/]?\s*"
                rf"{re.escape(number)}"
                rf"(?!\d)"
            )

        return bool(
            re.search(
                pattern,
                title,
                re.IGNORECASE,
            )
        )

    # ============================================================
    # EXACT STANDARD MATCH IN URL
    # ============================================================

    @staticmethod
    def url_contains_exact_standard(
        url: str | None,
        standard_number: str,
    ) -> bool:

        if not url:
            return False

        normalized = (
            DocumentRepository
            .normalize_standard_number(
                standard_number
            )
        )

        if not normalized:
            return False

        match = re.fullmatch(
            r"IS\s+(\d{2,6})(?::(\d{4}))?",
            normalized,
        )

        if not match:
            return False

        number = match.group(1)
        year = match.group(2)

        # URL examples:
        #
        # IS-456
        # IS_456
        # IS/456
        #
        # But NOT:
        #
        # IS-4566
        #

        if year:

            pattern = (
                rf"(?<![A-Z0-9])"
                rf"IS[-_/]?"
                rf"{re.escape(number)}"
                rf"[-_/:\s]+"
                rf"{re.escape(year)}"
                rf"(?!\d)"
            )

        else:

            pattern = (
                rf"(?<![A-Z0-9])"
                rf"IS[-_/]?"
                rf"{re.escape(number)}"
                rf"(?!\d)"
            )

        return bool(
            re.search(
                pattern,
                url,
                re.IGNORECASE,
            )
        )

    # ============================================================
    # FIND STANDARD
    # ============================================================

    def find_standard(
        self,
        standard_number: str,
        limit: int = 10,
    ) -> list[Document]:

        normalized = (
            self.normalize_standard_number(
                standard_number
            )
        )

        if not normalized:
            return []

        match = re.fullmatch(
            r"IS\s+(\d{2,6})(?::(\d{4}))?",
            normalized,
        )

        if not match:
            return []

        number = match.group(1)
        year = match.group(2)

        # --------------------------------------------------------
        # Broad SQL candidate search.
        #
        # This may retrieve IS 4566 for an IS 456 query,
        # but the Python exact-match stage below will reject it.
        # --------------------------------------------------------

        candidates = (
            self.db.query(Document)
            .filter(
                or_(
                    Document.standard_number.ilike(
                        f"IS {number}"
                    ),

                    Document.standard_number.ilike(
                        f"IS {number}:%"
                    ),

                    Document.title.ilike(
                        f"%IS {number}%"
                    ),

                    Document.document_url.ilike(
                        f"%IS-{number}%"
                    ),

                    Document.document_url.ilike(
                        f"%IS_{number}%"
                    ),

                    Document.document_url.ilike(
                        f"%IS/{number}%"
                    ),
                )
            )
            .all()
        )

        ranked: list[
            tuple[int, Document]
        ] = []

        for doc in candidates:

            score = 0

            doc_standard = (
                self.normalize_standard_number(
                    doc.standard_number
                )
            )

            # ----------------------------------------------------
            # EXACT DB STANDARD
            # ----------------------------------------------------

            if doc_standard == normalized:

                score += 100

            # ----------------------------------------------------
            # EXACT TITLE
            # ----------------------------------------------------

            if self.title_contains_exact_standard(
                doc.title,
                normalized,
            ):

                score += 70

            # ----------------------------------------------------
            # EXACT URL
            # ----------------------------------------------------

            if self.url_contains_exact_standard(
                doc.document_url,
                normalized,
            ):

                score += 40

            # ----------------------------------------------------
            # DOCUMENT TYPE
            # ----------------------------------------------------

            if (
                doc.document_type
                == "Indian Standard"
            ):

                score += 20

            # ----------------------------------------------------
            # CATEGORY
            # ----------------------------------------------------

            if doc.category == "Standards":

                score += 10

            # ----------------------------------------------------
            # CRITICAL SAFETY CHECK
            #
            # A candidate MUST have an exact standard match.
            #
            # This prevents:
            #
            # IS 456 -> IS 4566
            #
            # ----------------------------------------------------

            exact_match = False

            if doc_standard == normalized:
                exact_match = True

            if self.title_contains_exact_standard(
                doc.title,
                normalized,
            ):
                exact_match = True

            if self.url_contains_exact_standard(
                doc.document_url,
                normalized,
            ):
                exact_match = True

            if not exact_match:
                continue

            # ----------------------------------------------------
            # YEAR FILTER
            # ----------------------------------------------------

            if year:

                exact_year = False

                if doc_standard == normalized:
                    exact_year = True

                if self.title_contains_exact_standard(
                    doc.title,
                    normalized,
                ):
                    exact_year = True

                if self.url_contains_exact_standard(
                    doc.document_url,
                    normalized,
                ):
                    exact_year = True

                if not exact_year:
                    continue

            if score > 0:

                ranked.append(
                    (score, doc)
                )

        # --------------------------------------------------------
        # SORT
        # --------------------------------------------------------

        ranked.sort(
            key=lambda item: (
                -item[0],

                -(
                    item[1].last_updated.timestamp()
                    if item[1].last_updated
                    else 0
                ),

                -(
                    item[1].effective_date.timestamp()
                    if item[1].effective_date
                    else 0
                ),

                -(
                    item[1].publication_date.timestamp()
                    if item[1].publication_date
                    else 0
                ),

                -(item[1].id or 0),
            )
        )

        return [
            doc
            for _, doc in ranked[:limit]
        ]

    # ============================================================
    # DETERMINISTIC METADATA SCORING
    # ============================================================

    @staticmethod
    def calculate_metadata_score(
        doc: Document,
        query,
    ) -> int:
        """
        Calculate deterministic metadata score based on exact specification:
        - exact standard number: +50
        - exact document_type: +40
        - exact category: +30
        - exact subcategory: +20
        - title phrase match: +25
        - title token match: +10 per meaningful token
        - keyword match: +10
        - description match: +5
        - language match: +10
        - latest/revision requirement: +10
        """
        score = 0

        # 1. Exact standard number (+50)
        query_standard = getattr(query, "standard_number", None)
        if query_standard:
            norm_query_standard = DocumentRepository.normalize_standard_number(query_standard)
            norm_doc_standard = DocumentRepository.normalize_standard_number(doc.standard_number)
            if (norm_query_standard and norm_doc_standard and norm_query_standard == norm_doc_standard) or \
               (norm_query_standard and DocumentRepository.title_contains_exact_standard(doc.title, norm_query_standard)) or \
               (norm_query_standard and DocumentRepository.url_contains_exact_standard(doc.document_url, norm_query_standard)):
                score += 50

        # 2. Exact document_type (+40)
        query_doc_type = getattr(query, "document_type", None)
        if query_doc_type and doc.document_type:
            if doc.document_type.strip().lower() == query_doc_type.strip().lower():
                score += 40

        # 3. Exact category (+30)
        query_category = getattr(query, "category", None)
        if query_category and doc.category:
            if doc.category.strip().lower() == query_category.strip().lower():
                score += 30

        # 4. Exact subcategory (+20)
        query_subcategory = getattr(query, "subcategory", None)
        if query_subcategory and doc.subcategory:
            if doc.subcategory.strip().lower() == query_subcategory.strip().lower():
                score += 20

        # 5. Title phrase match (+25)
        search_text = (getattr(query, "search_text", None) or "").strip().lower()
        title_lower = (doc.title or "").lower()
        if search_text and len(search_text) >= 3 and search_text in title_lower:
            score += 25

        # 6. Title token match (+10 per meaningful token)
        tokens = DocumentRepository._tokenize(search_text) if search_text else []
        for token in tokens:
            if token in title_lower:
                score += 10

        # 7. Keyword match (+10)
        keywords_lower = (doc.keywords or "").lower()
        if tokens and any(token in keywords_lower for token in tokens):
            score += 10

        # 8. Description match (+5)
        desc_lower = (doc.description or "").lower()
        if tokens and any(token in desc_lower for token in tokens):
            score += 5

        # 9. Language match (+10)
        query_language = getattr(query, "language", None)
        if query_language and doc.language:
            if doc.language.strip().lower() == query_language.strip().lower():
                score += 10

        # 10. Latest/revision requirement (+10)
        needs_latest = getattr(query, "needs_latest", False)
        if needs_latest and (doc.revision or doc.edition):
            score += 10

        return score

    # ============================================================
    # QUERY-BASED SEARCH
    # ============================================================

    def search_from_query(
        self,
        query,
        limit: int | None = None,
    ) -> list[Document]:

        limit = (
            limit
            or getattr(
                query,
                "limit",
                None,
            )
            or 10
        )

        # --------------------------------------------------------
        # 1. STANDARD NUMBER SEARCH (Fast path if standard given)
        # --------------------------------------------------------

        if getattr(
            query,
            "standard_number",
            None,
        ):

            results = self.find_standard(
                query.standard_number,
                limit=limit * 2,
            )

            if results:
                ranked: list[tuple[int, Document]] = []
                for doc in results:
                    score = self.calculate_metadata_score(doc, query)
                    ranked.append((score, doc))

                ranked.sort(
                    key=lambda item: (
                        -item[0],
                        -(
                            item[1].last_updated.timestamp()
                            if item[1].last_updated
                            else 0
                        ),
                        -(
                            item[1].effective_date.timestamp()
                            if item[1].effective_date
                            else 0
                        ),
                        -(
                            item[1].publication_date.timestamp()
                            if item[1].publication_date
                            else 0
                        ),
                        -(item[1].id or 0),
                    )
                )

                return [doc for _, doc in ranked[:limit]]

        # --------------------------------------------------------
        # 2. GENERAL CANDIDATE RETRIEVAL
        # --------------------------------------------------------

        search_text = (
            getattr(
                query,
                "search_text",
                None,
            )
            or ""
        ).strip()

        tokens = self._tokenize(search_text) if search_text else []

        conditions = []

        for token in tokens:
            pattern = f"%{token}%"
            conditions.append(Document.title.ilike(pattern))
            conditions.append(Document.description.ilike(pattern))
            conditions.append(Document.keywords.ilike(pattern))

        if getattr(query, "category", None):
            conditions.append(Document.category.ilike(f"%{query.category}%"))

        if getattr(query, "subcategory", None):
            conditions.append(Document.subcategory.ilike(f"%{query.subcategory}%"))

        if getattr(query, "document_type", None):
            conditions.append(Document.document_type.ilike(f"%{query.document_type}%"))

        if conditions:
            candidates = (
                self.db.query(Document)
                .filter(or_(*conditions))
                .limit(2000)
                .all()
            )
        else:
            candidates = (
                self.db.query(Document)
                .order_by(Document.id.desc())
                .limit(2000)
                .all()
            )

        ranked: list[tuple[int, Document]] = []

        for doc in candidates:
            score = self.calculate_metadata_score(doc, query)
            if score > 0:
                ranked.append((score, doc))

        # --------------------------------------------------------
        # SORT DETERMINISTICALLY
        # --------------------------------------------------------

        ranked.sort(
            key=lambda item: (
                -item[0],
                -(
                    item[1].last_updated.timestamp()
                    if item[1].last_updated
                    else 0
                ),
                -(
                    item[1].effective_date.timestamp()
                    if item[1].effective_date
                    else 0
                ),
                -(
                    item[1].publication_date.timestamp()
                    if item[1].publication_date
                    else 0
                ),
                -(item[1].id or 0),
            )
        )

        return [
            doc
            for _, doc in ranked[:limit]
        ]

    # ============================================================
    # TOKENIZATION
    # ============================================================

    @staticmethod
    def _tokenize(
        text: str,
    ) -> list[str]:

        tokens = re.findall(
            r"[a-zA-Z0-9]+",
            text.lower(),
        )

        stop_words = {
            "the",
            "a",
            "an",
            "for",
            "of",
            "to",
            "in",
            "on",
            "and",
            "or",
            "is",
            "are",
            "find",
            "show",
            "give",
            "get",
            "latest",
            "version",
            "standard",
            "standards",
            "related",
            "document",
            "documents",
        }

        result = []

        for token in tokens:

            if token in stop_words:
                continue

            if len(token) < 2:
                continue

            if token not in result:
                result.append(token)

        return result

    # ============================================================
    # STATUS HELPERS
    # ============================================================

    def get_unparsed(
        self,
        limit: int = 100,
    ) -> list[Document]:

        return (
            self.db.query(Document)
            .filter(
                Document.parse_status
                == "NOT_PARSED"
            )
            .limit(limit)
            .all()
        )

    def get_unembedded(
        self,
        limit: int = 100,
    ) -> list[Document]:

        return (
            self.db.query(Document)
            .filter(
                Document.embedding_status
                == "NOT_EMBEDDED"
            )
            .limit(limit)
            .all()
        )

    def mark_parsed(
        self,
        document_id: int,
        quality_score: float = 0.0,
    ) -> None:

        doc = self.get_by_id(
            document_id
        )

        if not doc:
            return

        doc.parse_status = "PARSED"
        doc.parse_quality_score = (
            quality_score
        )

        self.db.commit()

    def mark_parse_failed(
        self,
        document_id: int,
    ) -> None:

        doc = self.get_by_id(
            document_id
        )

        if not doc:
            return

        doc.parse_status = "FAILED"

        self.db.commit()

    # ============================================================
    # COUNTS
    # ============================================================

    def counts(self) -> dict:

        total = (
            self.db.query(Document)
            .count()
        )

        unparsed = (
            self.db.query(Document)
            .filter(
                Document.parse_status
                == "NOT_PARSED"
            )
            .count()
        )

        embedded = (
            self.db.query(Document)
            .filter(
                Document.embedding_status
                == "EMBEDDED"
            )
            .count()
        )

        return {
            "total": total,
            "unparsed": unparsed,
            "embedded": embedded,
        }
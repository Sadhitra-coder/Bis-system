from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.query_understanding import (
    QueryUnderstandingAgent,
)
from app.database.repositories.document import (
    DocumentRepository,
)
from app.schemas.queries import QueryUnderstanding


class MetadataRetrievalService:
    """
    Connects Query Understanding with PostgreSQL metadata.

    Flow:

        User Query
             ↓
        Query Understanding
             ↓
        PostgreSQL metadata search
             ↓
        Candidate Documents
    """

    def __init__(self, db: Session):

        self.db = db

        self.repository = DocumentRepository(
            db
        )

        self.query_agent = QueryUnderstandingAgent()

    def retrieve(
        self,
        user_query: str,
    ) -> tuple[
        QueryUnderstanding,
        list,
    ]:

        # -----------------------------------------------------
        # STEP 1: Understand query
        # -----------------------------------------------------

        structured_query = (
            self.query_agent.understand(
                user_query
            )
        )

        # -----------------------------------------------------
        # STEP 2: Search PostgreSQL
        # -----------------------------------------------------

        documents = (
            self.repository.search_from_query(
                structured_query
            )
        )

        return (
            structured_query,
            documents,
        )
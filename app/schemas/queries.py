from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class QueryUnderstanding(BaseModel):

    intent: str = Field(
        default="search_documents"
    )

    search_text: Optional[str] = None

    document_type: Optional[str] = None

    category: Optional[str] = None

    subcategory: Optional[str] = None

    standard_number: Optional[str] = None

    language: Optional[str] = None

    needs_latest: bool = False

    limit: int = Field(
        default=10,
        ge=1,
        le=50,
    )
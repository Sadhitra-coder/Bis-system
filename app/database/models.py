from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    base_url = Column(String(2048), nullable=False)
    source_type = Column(String(64), nullable=False, default="website")
    active = Column(Boolean, nullable=False, default=True)

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    pages = relationship(
        "Page",
        back_populates="source",
    )

    documents = relationship(
        "Document",
        back_populates="source",
    )


class Page(Base):
    __tablename__ = "pages"

    id = Column(Integer, primary_key=True, index=True)

    source_id = Column(
        Integer,
        ForeignKey("sources.id"),
        nullable=False,
    )

    url = Column(String(2048), nullable=False)
    canonical_url = Column(
        String(2048),
        nullable=False,
        index=True,
    )

    title = Column(String(1024), nullable=True)
    page_type = Column(String(128), nullable=True)
    parent_url = Column(String(2048), nullable=True)

    category = Column(String(255), nullable=True)
    subcategory = Column(String(255), nullable=True)

    content_hash = Column(String(255), nullable=True)

    crawl_status = Column(
        String(64),
        nullable=False,
        default="DISCOVERED",
    )

    last_crawled_at = Column(
        DateTime,
        nullable=True,
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    source = relationship(
        "Source",
        back_populates="pages",
    )

    documents = relationship(
        "Document",
        back_populates="page",
    )

    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "canonical_url",
            name="uq_page_source_canonical_url",
        ),
    )


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)

    page_id = Column(
        Integer,
        ForeignKey("pages.id"),
        nullable=True,
    )

    source_id = Column(
        Integer,
        ForeignKey("sources.id"),
        nullable=True,
    )

    title = Column(
        String(1024),
        nullable=False,
    )

    document_url = Column(
        String(2048),
        nullable=False,
    )

    canonical_url = Column(
        String(2048),
        nullable=False,
        index=True,
    )

    document_type = Column(
        String(128),
        nullable=True,
    )

    category = Column(
        String(255),
        nullable=True,
    )

    subcategory = Column(
        String(255),
        nullable=True,
    )

    standard_number = Column(
        String(128),
        nullable=True,
    )

    revision = Column(
        String(64),
        nullable=True,
    )

    edition = Column(
        String(64),
        nullable=True,
    )

    description = Column(
        Text,
        nullable=True,
    )

    keywords = Column(
        Text,
        nullable=True,
    )

    language = Column(
        String(32),
        nullable=True,
    )

    publication_date = Column(
        DateTime,
        nullable=True,
    )

    effective_date = Column(
        DateTime,
        nullable=True,
    )

    last_updated = Column(
        DateTime,
        nullable=True,
    )

    mime_type = Column(
        String(128),
        nullable=True,
    )

    file_size = Column(
        Integer,
        nullable=True,
        default=0,
    )

    content_hash = Column(
        String(255),
        nullable=True,
    )

    crawl_status = Column(
        String(64),
        nullable=False,
        default="DISCOVERED",
    )

    parse_status = Column(
        String(64),
        nullable=False,
        default="NOT_PARSED",
    )

    embedding_status = Column(
        String(64),
        nullable=False,
        default="NOT_EMBEDDED",
    )

    parse_quality_score = Column(
        Float,
        nullable=True,
        default=0.0,
    )

    last_crawled_at = Column(
        DateTime,
        nullable=True,
    )

    last_parsed_at = Column(
        DateTime,
        nullable=True,
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    # Page relationship
    page = relationship(
        "Page",
        back_populates="documents",
    )

    # Source relationship
    source = relationship(
        "Source",
        back_populates="documents",
    )

    # Document versions
    versions = relationship(
        "DocumentVersion",
        back_populates="document",
    )

    # Relationships where this document is the SOURCE
    relationships = relationship(
        "DocumentRelationship",
        foreign_keys="DocumentRelationship.source_document_id",
        back_populates="source_document",
    )

    # Relationships where this document is the TARGET
    target_relationships = relationship(
        "DocumentRelationship",
        foreign_keys="DocumentRelationship.target_document_id",
        back_populates="target_document",
    )

    # Chunks relationship
    chunks = relationship(
        "DocumentChunk",
        back_populates="document",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "canonical_url",
            name="uq_document_source_canonical_url",
        ),
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    document_id = Column(
        Integer,
        ForeignKey("documents.id"),
        nullable=False,
    )

    version_label = Column(
        String(128),
        nullable=True,
    )

    revision = Column(
        String(64),
        nullable=True,
    )

    document_url = Column(
        String(2048),
        nullable=False,
    )

    content_hash = Column(
        String(255),
        nullable=True,
    )

    publication_date = Column(
        DateTime,
        nullable=True,
    )

    effective_date = Column(
        DateTime,
        nullable=True,
    )

    is_current = Column(
        Boolean,
        default=False,
        nullable=False,
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    document = relationship(
        "Document",
        back_populates="versions",
    )


class DocumentRelationship(Base):
    __tablename__ = "document_relationships"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    source_document_id = Column(
        Integer,
        ForeignKey("documents.id"),
        nullable=False,
    )

    target_document_id = Column(
        Integer,
        ForeignKey("documents.id"),
        nullable=False,
    )

    relationship_type = Column(
        String(64),
        nullable=False,
    )

    confidence = Column(
        Float,
        nullable=True,
        default=0.0,
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    # Explicitly tell SQLAlchemy which FK this relationship uses
    source_document = relationship(
        "Document",
        foreign_keys=[source_document_id],
        back_populates="relationships",
    )

    # Explicitly tell SQLAlchemy which FK this relationship uses
    target_document = relationship(
        "Document",
        foreign_keys=[target_document_id],
        back_populates="target_relationships",
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    chunk_id = Column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )

    text = Column(
        Text,
        nullable=False,
    )

    page_start = Column(
        Integer,
        nullable=True,
    )

    page_end = Column(
        Integer,
        nullable=True,
    )

    section = Column(
        String(512),
        nullable=True,
    )

    heading = Column(
        String(512),
        nullable=True,
    )

    source_file = Column(
        String(1024),
        nullable=True,
    )

    embedding = Column(
        ARRAY(Float),
        nullable=True,
    )

    chunk_metadata = Column(
        Text,
        nullable=True,
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    document = relationship(
        "Document",
        back_populates="chunks",
    )
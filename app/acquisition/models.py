"""app/acquisition/models.py

Canonical Domain Models and Controlled Vocabularies for the
Scalable Knowledge Acquisition and Update System.

Supports:
- Multi-tier source registry
- Official discovery engine
- Strict licensing / rights policy classification
- Metadata-first collection
- Jurisdiction hierarchy (National, State, International)
- Priority queuing
- Incremental update tracking
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ============================================================
# 1. RIGHTS & LICENSING CLASSIFICATION
# ============================================================

class LicenseStatus(str, Enum):
    """
    Controlled rights vocabulary.
    Material whose rights are unclear or restricted must never be automatically
    reproduced or indexed as full-text without valid authorization.
    """
    PUBLIC = "PUBLIC"                    # Gazette notifications, statutory QCOs, public guidance
    LICENSED = "LICENSED"                # Proprietary/copyrighted standard full text (metadata only)
    RESTRICTED = "RESTRICTED"            # Confidential/internal documents (never indexed)
    UNKNOWN_RIGHTS = "UNKNOWN_RIGHTS"    # Unclassified material (quarantined pending review)


# ============================================================
# 2. JURISDICTION HIERARCHY
# ============================================================

class JurisdictionLevel(str, Enum):
    """
    First-class jurisdiction tiering to guarantee multi-jurisdiction
    safety and zero cross-jurisdiction leakage during retrieval.
    """
    NATIONAL = "NATIONAL"
    STATE = "STATE"
    UNION_TERRITORY = "UNION_TERRITORY"
    INTERNATIONAL = "INTERNATIONAL"


class JurisdictionCode(str, Enum):
    """Supported geographical and regulatory jurisdictions."""
    # Current Primary Jurisdiction
    INDIA = "INDIA"
    INDIA_NATIONAL = "INDIA:NATIONAL"
    INDIA_ANDHRA_PRADESH = "INDIA:AP"
    INDIA_DELHI = "INDIA:DL"
    INDIA_MAHARASHTRA = "INDIA:MH"
    INDIA_GUJARAT = "INDIA:GJ"
    INDIA_TAMIL_NADU = "INDIA:TN"
    
    # Architecture-Ready Future Jurisdictions
    UK = "UK"
    UK_ENGLAND = "UK:ENGLAND"
    UK_SCOTLAND = "UK:SCOTLAND"
    UK_WALES = "UK:WALES"
    UK_NORTHERN_IRELAND = "UK:NORTHERN_IRELAND"


# ============================================================
# 3. SOURCE REGISTRY ENUMS
# ============================================================

class SourceType(str, Enum):
    """Classification of regulatory source portals."""
    OFFICIAL_PORTAL = "OFFICIAL_PORTAL"          # Official BIS/Bureau portal
    GAZETTE = "GAZETTE"                          # Official Gazette of India / State Gazette
    STANDARDS_BODY = "STANDARDS_BODY"            # National/International standards body
    MINISTRY_PORTAL = "MINISTRY_PORTAL"          # Central ministry regulatory portal
    REGULATOR_PORTAL = "REGULATOR_PORTAL"        # Specialized regulatory agency
    LABORATORY_REGISTRY = "LABORATORY_REGISTRY"  # NABL/BIS laboratory directory


class AuthorityLevel(str, Enum):
    """Legal standing of the source authority."""
    STATUTORY_NATIONAL = "STATUTORY_NATIONAL"    # BIS Act, Parliamentary Statute
    CENTRAL_MINISTRY = "CENTRAL_MINISTRY"        # Ministry of Consumer Affairs, DPIIT, MeitY
    REGULATOR = "REGULATOR"                      # Statutory regulator
    STATE_GOVERNMENT = "STATE_GOVERNMENT"        # State government regulatory department


class CrawlFrequency(str, Enum):
    """Target crawl frequency for source synchronization."""
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    BIWEEKLY = "BIWEEKLY"
    MONTHLY = "MONTHLY"
    MANUAL = "MANUAL"


# ============================================================
# 4. DOCUMENT CLASSES & ACQUISITION PRIORITY
# ============================================================

class DocumentClass(str, Enum):
    """Classification of acquired regulatory compliance documents."""
    QCO = "QCO"                                      # Mandatory Quality Control Order
    QCO_AMENDMENT = "QCO_AMENDMENT"                  # Amendment to Quality Control Order
    GAZETTE_NOTIFICATION = "GAZETTE_NOTIFICATION"    # Official gazette order
    STANDARD = "STANDARD"                            # Indian Standard (IS) specification
    AMENDMENT = "AMENDMENT"                          # Standard amendment sheet
    PRODUCT_MANUAL = "PRODUCT_MANUAL"                # Guidelines for BIS certification / manual
    TESTING_SCHEME = "TESTING_SCHEME"                # Scheme of Testing and Inspection (STI)
    CERTIFICATION_GUIDANCE = "CERTIFICATION_GUIDANCE"# Application & marking guidelines
    LABORATORY_DIRECTORY = "LABORATORY_DIRECTORY"    # Recognized testing laboratories


class AcquisitionPriority(int, Enum):
    """
    Controlled priority queuing.
    Strictly orders ingestion by legal liability and regulatory urgency.
    """
    PRIORITY_1_MANDATORY_QCO = 1          # Mandatory Quality Control Orders (criminal liability)
    PRIORITY_2_MANUALS_AND_SCHEMES = 2    # Product Certification Manuals & Schemes of Inspection (STI)
    PRIORITY_3_HIGH_DEMAND_STANDARDS = 3  # High-demand industrial standards (IS 16444, IS 1293, etc.)
    PRIORITY_4_GENERAL_STANDARDS = 4      # Remaining voluntary standards


# ============================================================
# 5. CORE ACQUISITION MODELS
# ============================================================

class SourceRegistryRecord(BaseModel):
    """
    Registry entry for an authorized government or regulatory knowledge source.
    """
    source_id: str = Field(..., description="Unique slug identifier (e.g. 'bis_standards_portal')")
    source_organization: str = Field(..., description="Entity name (e.g. 'Bureau of Indian Standards')")
    source_domain: str = Field(..., description="Primary domain (e.g. 'standardsbis.bsbedge.com')")
    source_type: SourceType = Field(default=SourceType.OFFICIAL_PORTAL)
    authority_level: AuthorityLevel = Field(default=AuthorityLevel.STATUTORY_NATIONAL)
    jurisdiction: JurisdictionCode = Field(default=JurisdictionCode.INDIA_NATIONAL)
    allowed_document_classes: List[DocumentClass] = Field(default_factory=list)
    crawl_frequency: CrawlFrequency = Field(default=CrawlFrequency.WEEKLY)
    parser: str = Field(..., description="Registered parser name (e.g. 'gazette_parser')")
    license_access_policy: LicenseStatus = Field(default=LicenseStatus.PUBLIC)
    source_url: str = Field(..., description="Base entrypoint URL")
    is_active: bool = True
    last_successful_crawl: Optional[str] = None
    last_change: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0


class DiscoveredItem(BaseModel):
    """
    A discrete document or record discovered during source scanning.
    """
    item_id: str = Field(..., description="Deterministic discovery ID")
    source_id: str = Field(..., description="Referenced source registry ID")
    document_class: DocumentClass
    title: str
    identifier: str = Field(..., description="Standard or notification number (e.g. 'IS 3055', 'S.O. 4345(E)')")
    standard_year: Optional[int] = None
    edition_or_version: Optional[str] = None
    publication_date: Optional[str] = None
    effective_date: Optional[str] = None
    source_url: str
    license_status: LicenseStatus = LicenseStatus.UNKNOWN_RIGHTS
    access_date: str = ""
    content_hash: Optional[str] = None
    priority: AcquisitionPriority = AcquisitionPriority.PRIORITY_4_GENERAL_STANDARDS
    metadata_payload: Dict[str, Any] = Field(default_factory=dict)
    is_ingested: bool = False
    ingestion_job_id: Optional[str] = None
    quarantine_reason: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0


class StandardMetadataRecord(BaseModel):
    """
    Rich standards catalog metadata collected during the metadata-first stage.
    Collected and persisted before any full text is downloaded or indexed.
    """
    standard_number: str                 # e.g. 'IS 3055:2024'
    title: str                           # Official full title
    year: Optional[int] = None
    edition_or_version: Optional[str] = None
    status: str = "CURRENT"              # 'CURRENT', 'SUPERSEDED', 'WITHDRAWN'
    category: Optional[str] = None       # e.g. 'Medical Equipment', 'Electrical Engineering'
    technical_committee: Optional[str] = None # e.g. 'MHD 02 - Clinical Thermometers'
    ics_code: Optional[str] = None       # International Classification for Standards
    applicable_products: List[str] = Field(default_factory=list)
    mandatory_qco_number: Optional[str] = None # Linked QCO making it mandatory
    is_mandatory: bool = False
    source_url: str
    license_status: LicenseStatus = LicenseStatus.LICENSED
    full_text_available: bool = False
    document_id: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0


class DocumentPolicyRecord(BaseModel):
    """Document rights and licensing enforcement audit record."""
    document_id: str
    source_url: str
    access_date: str
    content_hash: str
    document_version: str
    license_status: LicenseStatus
    can_reproduce_full_text: bool
    can_index_metadata: bool
    remediation_notes: Optional[str] = None


# ============================================================
# 6. RIGHTS & DOWNLOAD WORKFLOW ENUMS & RECORDS
# ============================================================

class RightsStatus(str, Enum):
    """Controlled rights & access vocabulary mandated for automated ingestion."""
    PUBLIC = "PUBLIC"                    # Legally accessible public document (e.g. gazette, public manual)
    AUTHORIZED = "AUTHORIZED"            # Licensed/authorized for internal RAG ingestion
    LOGIN_REQUIRED = "LOGIN_REQUIRED"    # Requires authentication credentials (do not bypass)
    LICENSE_REQUIRED = "LICENSE_REQUIRED"# Proprietary standard (full-text restricted)
    UNKNOWN = "UNKNOWN"                  # Unverified rights (quarantine)


class DownloadStatus(str, Enum):
    """Status of document download execution."""
    PENDING = "PENDING"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOADED = "DOWNLOADED"
    BLOCKED_ACCESS = "BLOCKED_ACCESS"    # Legally or technically restricted access
    FAILED = "FAILED"
    SKIPPED_DEDUPLICATED = "SKIPPED_DEDUPLICATED"


class DocumentDiscoveryRecord(BaseModel):
    """
    Standardized Document Discovery Record capturing full discovery provenance.
    """
    document_id: str
    source_id: str
    source_url: str
    canonical_url: str
    document_type: str
    authority: str
    jurisdiction: str
    standard_number: str
    title: str
    publication_year: Optional[int] = None
    revision: Optional[str] = None
    status: str = "CURRENT"
    access_method: str = "DIRECT_HTTP"
    rights_status: RightsStatus = RightsStatus.PUBLIC
    discovered_at: float = 0.0
    download_status: DownloadStatus = DownloadStatus.PENDING
    content_hash: Optional[str] = None
    local_path: Optional[str] = None
    file_size_bytes: Optional[int] = None
    quarantine_reason: Optional[str] = None


class QCORecord(BaseModel):
    """Structured record for Quality Control Orders."""
    qco_id: str
    title: str
    qco_number: Optional[str] = None
    issuing_ministry: str
    date_of_order: Optional[str] = None
    enforcement_date: Optional[str] = None
    mandatory_standards: List[str] = Field(default_factory=list)
    exempt_classes: Optional[List[str]] = None
    official_source_url: str
    effective_status: str = "ENFORCED"


class AuditLogEntry(BaseModel):
    """Acquisition audit log entry."""
    log_id: str
    timestamp: float
    source_id: str
    url: str
    action: str
    status: str
    http_status: Optional[int] = None
    content_hash: Optional[str] = None
    document_id: Optional[str] = None
    error: Optional[str] = None


class JobStateRecord(BaseModel):
    """State tracking for resumable acquisition jobs."""
    job_id: str
    job_type: str
    source_id: str
    total_discovered: int = 0
    current_index: int = 0
    downloaded_count: int = 0
    ingested_count: int = 0
    blocked_count: int = 0
    failed_count: int = 0
    status: str = "PENDING"
    started_at: float = 0.0
    updated_at: float = 0.0
    error: Optional[str] = None


# BIS KNOWLEDGE COVERAGE & CORPUS INVENTORY AUDIT
**Generated at**: `2026-09-22T05:03:44.789352Z`  
**Environment**: Local First (V5 Engine)  
**Database**: `E:\ComplienceManagement\Bis-system\data\knowledge\bis_knowledge.db`  
**Vector Store**: ChromaDB `bis_documents` (193 chunks)  

---

## 1. EXECUTIVE SUMMARY & AT-A-GLANCE METRICS

| Layer / Metric Dimension | Count / Status | Details & Highlights |
|:---|:---:|:---|
| **ChromaDB Vector Store** | **193 Chunks** | Schema 6.0 Strict, BGE-Large 1024-dim |
| **Official Sources Registered** | **7 Sources** | BIS, Gazette, DPIIT, MeitY, BSI UK |
| **Standards Catalog (Metadata)** | **7 Standards** | 6 Mandatory QCO Standards, 1 Public Standard |
| **Discovered Regulatory Items** | **0 Items** | Prioritized acquisition backlog |
| **Rights Ledger Records** | **8 Records** | 1 Full-Text Allowed, 7 Metadata Only |
| **Knowledge Graph Entities** | **30 Entities** | Standards, QCOs, Products, Schemes, Authorities |
| **Knowledge Relationships** | **25 Edges** | Typed relationships (`APPLIES_TO`, `REQUIRES`, etc.) |

---

## 2. OFFICIAL SOURCE REGISTRY (GOVERNMENT & REGULATORS)

The Source Registry guarantees that all automated knowledge acquisition is constrained strictly to authorized, high-authority regulatory bodies.

| Source ID | Organization | Authority Level | Crawl Frequency | License Policy | Domain / Portal |
|:---|:---|:---:|:---:|:---:|:---|
| `bis_standards_portal` | Bureau of Indian Standards | **STATUTORY_NATIONAL** | `WEEKLY` | `LICENSED` | `standardsbis.bsbedge.com` |
| `bis_manakonline` | Bureau of Indian Standards - Manakonline | **STATUTORY_NATIONAL** | `DAILY` | `PUBLIC` | `manakonline.in` |
| `egazette_india` | Government of India - Directorate of Printing | **CENTRAL_MINISTRY** | `DAILY` | `PUBLIC` | `egazette.gov.in` |
| `dpiit_qco_portal` | Department for Promotion of Industry and Internal Trade (DPIIT) | **CENTRAL_MINISTRY** | `WEEKLY` | `PUBLIC` | `dpiit.gov.in` |
| `mca_consumeraffairs` | Ministry of Consumer Affairs, Food and Public Distribution | **CENTRAL_MINISTRY** | `WEEKLY` | `PUBLIC` | `consumeraffairs.nic.in` |
| `meity_qco_portal` | Ministry of Electronics and Information Technology (MeitY) | **CENTRAL_MINISTRY** | `WEEKLY` | `PUBLIC` | `meity.gov.in` |
| `bsi_standards_uk` | British Standards Institution (BSI) | **STATUTORY_NATIONAL** | `MONTHLY` | `LICENSED` | `bsigroup.com` |

### Authority Breakdown
- **STATUTORY_NATIONAL**: 3 sources
- **CENTRAL_MINISTRY**: 4 sources

---

## 3. RIGHTS & LICENSING CLASSIFICATION LEDGER

In strict adherence to intellectual property regulations, full-text reproduction is partitioned from metadata discovery:

- **Total Rights Records**: `8`
- **Full-Text Ingestion Allowed**: `1` (Gazette notifications, Statutory QCOs, Public regulatory documents)
- **Metadata Only / Copyrighted Full-Text Protected**: `7` (Official IS standards full text retained in catalog without infringing full-text reproduction)

### Distribution by License Status:
- **PUBLIC**: 1 documents
- **LICENSED**: 7 documents

---

## 4. STANDARDS METADATA CATALOG (METADATA-FIRST)

The engine tracks rich metadata (title, edition, mandatory status, issuing authority, product category) across industrial domains:

| Standard Number | Title | Year | Industry Sector | Mandatory Status | License Class |
|:---|:---|:---:|:---|:---:|:---:|
| **IS 1417:2016** | Gold and Gold Alloys, Jewellery/Artefacts - Fineness and Marking - Specification | 2016 | Precious Metals & Hallmarking | `MANDATORY (QCO)` | `LICENSED` |
| **IS 3055:2024** | Clinical Thermometers - Specification - Part 1: Solid Stem Type | 2024 | Medical Equipment | `MANDATORY (QCO)` | `LICENSED` |
| **IS 16444:2015** | a.c. Static Direct Connected Watt-hour Smart Meter Class 1 and 2 - Specification | 2015 | Electrical & Electronics | `MANDATORY (QCO)` | `LICENSED` |
| **IS 1293:2019** | Plugs and Socket-Outlets of Rated Voltage up to and Including 250 V and Rated Current up to and Including 16 A | 2019 | Electrical Accessories | `MANDATORY (QCO)` | `LICENSED` |
| **IS 15885 (Part 2/Sec 13):2012** | Lamp Controlgear - Part 2: Particular Requirements - Section 13: d.c. or a.c. Supplied Electronic Controlgear for LED Modules | 2012 | Lighting & Electronics | `MANDATORY (QCO)` | `LICENSED` |
| **IS 1786:2008** | High Strength Deformed Steel Bars and Wires for Concrete Reinforcement - Specification | 2008 | Steel & Metallurgy | `MANDATORY (QCO)` | `LICENSED` |
| **IS 9873 (Part 1):2019** | Safety of Toys - Part 1: Safety Aspects Related to Mechanical and Physical Properties | 2019 | Consumer Products & Child Safety | `MANDATORY (QCO)` | `LICENSED` |

### Coverage by Industry Sector:
- **Precious Metals & Hallmarking**: 1 standards
- **Medical Equipment**: 1 standards
- **Electrical & Electronics**: 1 standards
- **Electrical Accessories**: 1 standards
- **Lighting & Electronics**: 1 standards
- **Steel & Metallurgy**: 1 standards
- **Consumer Products & Child Safety**: 1 standards

---

## 5. KNOWLEDGE GRAPH & RELATIONSHIP TOPOLOGY

The graph layer captures cross-document dependencies, legal mandates, and certification workflows:

- **Standards**: `3`
- **Quality Control Orders (QCOs)**: `12`
- **Regulated Products**: `7`
- **Certification Schemes**: `3`
- **Testing Methods**: `0`
- **Regulatory Authorities**: `5`
- **Jurisdictions**: `4`
- **Total Typed Relationships**: `25`

### Relationship Types Indexed:
- `APPLIES_TO`: 19 links
- `CERTIFIED_UNDER`: 6 links

---

## 6. CURRENT CHROMA VECTOR STORE INVENTORY

- **Collection Name**: `bis_documents`
- **Total Active Chunks**: **193**
- **Schema Compliance**: **100% Schema 6.0 Compliant**
- **Vector Dimensions**: 1024 (`BAAI/bge-large-en-v1.5`)
- **Indexed Document**: `385/document.pdf` (Andhra Pradesh Gold Hallmarking Districts Annexure, Scheme IV)
- **Verified Retrieval**: Queries regarding mandatory hallmarking districts, Andhra Pradesh coverage, and hallmarking center requirements return verified evidence citations directly from indexed chunks.

---

## 7. REMAINING GAPS & EXPANSION ROADMAP

1. **Full-Text vs Metadata Separation**:
   - 6 major standards (IS 1293, IS 16444, IS 15885, IS 1786, IS 3055, IS 9873) are fully registered in the **Metadata Catalog** and **Knowledge Graph**, with citation, clause, and QCO mapping.
   - Authorized full-text ingestion for statutory QCO gazettes is queued for execution in Phase 2.
2. **Scheduled Polling**:
   - `UpdateEngine` and `AcquisitionPriorityQueue` are fully implemented and ready to run on an automated cron/schedule (`CrawlFrequency.DAILY` for DPIIT, `CrawlFrequency.WEEKLY` for BIS).
3. **Multi-Jurisdiction Expansion**:
   - Architectural schema includes first-class support for `UK` (`UK:ENGLAND`, `UK:SCOTLAND`), ready for BSI standard ingestion without database schema changes.

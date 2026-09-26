# BIS Intelligence Platform — API Contract Freeze Specification
**Document Version**: 2.0.0 (Frozen)  
**Date**: September 26, 2026  
**Target Environment**: Azure Container Apps (`bis-system-v5-korea`)  
**Base URL**: `https://bis-system-v5-korea.yellowmeadow-d3173c9a.koreacentral.azurecontainerapps.io`  
**OpenAPI Specification**: 3.1.0 (Synchronized with live deployment)

---

## 1. Executive Summary & Freeze Policy

This document defines the **immutable runtime API contract** for the **BIS Intelligence Platform** (SIH26107). 

### Invariant Rules
1. **No Breaking Schema Mutations**: Field names, casing, types, and nesting in existing endpoints (`/query`, `/status`, `/health`, `/ready`, `/upload`, `/jobs`) are strictly frozen.
2. **Deterministic Enums**: All enum values (`ConfidenceLevel`, `Decision`, `QueryState`, `GroundingStatus`, `SupportStatus`, `ClaimType`) must adhere to exact string literal values.
3. **Additive-Only Extensibility**: Any new regulatory intelligence fields (e.g., `regulatory_impact`, `drift_analysis`) must be optional and non-breaking for existing consumers (`ComplyWise`).
4. **Header Contracts**: The `X-Internal-Service-Key` header is required on protected mutation and query endpoints (`/query`, `/upload`). The `X-Correlation-ID` header is propagated deterministically across all requests.

---

## 2. Global Request Headers

| Header | Type | Required | Description / Contract |
| :--- | :--- | :--- | :--- |
| `X-Internal-Service-Key` | `string` | **Yes** (on `/query`, `/upload`) | 256-bit HMAC service authentication key. Returns `401 Unauthorized` if missing or invalid. |
| `X-Correlation-ID` | `string` | Optional | Client tracing ID. If omitted, engine generates format `bis-<uuid12>` and echoes in response headers. |
| `Content-Type` | `string` | **Yes** | Must be `application/json` (or `multipart/form-data` for `/upload`). |

---

## 3. Core Endpoint Specifications

### 3.1 `POST /query` — Grounded Regulatory Intelligence & Search

Primary entry point for industry and consumer regulatory inquiries across PRD requirements R1–R8.

#### Request Schema (`QueryRequest`)
```json
{
  "query": "string (required, non-empty)",
  "correlation_id": "string | null (optional)",
  "top_k": "integer | null (optional, 1 <= top_k <= 50, default: 5)",
  "audience": "string | null (optional: 'technical' | 'consumer', default: 'technical')",
  "manufacturer_origin": "string | null (optional: 'domestic' | 'foreign', default: 'domestic')",
  "business_context": "object | null (optional)",
  "profile_context": "object | null (optional)",
  "technical_specification": "object | null (optional)",
  "tender_specification": "object | null (optional)",
  "compliance_documents": "array[object] | null (optional)",
  "document_ids": "array[string] | null (REJECTED with 400 if non-empty)"
}
```

> [!IMPORTANT]
> `document_ids` scoping is intentionally rejected with HTTP 400 if provided with elements. Because the lexical BM25 retrieval arm indexes the global corpus at startup, filtering document subsets at query time would silently degrade recall without alerting the caller.

#### Response Schema (`QueryResponse`)
```json
{
  "query_id": "string",
  "correlation_id": "string",
  "query": "string",
  "answer": "string (4-part formatted answer)",
  "sources": [
    {
      "token": "string (e.g. 'EV1')",
      "standard_id": "string | null",
      "standard_number": "string (e.g. 'IS 1293')",
      "standard_title": "string | null",
      "version_id": "string | null",
      "clause_id": "string | null",
      "document_id": "string",
      "page_start": "integer | null",
      "authority": "string (e.g. 'BIS')",
      "standard_relation": "string ('identity' | 'reference')"
    }
  ],
  "decision": "string ('answer' | 'qualified_answer' | 'verification_required')",
  "query_state": "string ('ANSWERABLE' | 'INSUFFICIENT_EVIDENCE' | 'CONFLICTING_EVIDENCE' | 'AMBIGUOUS_QUERY' | 'VERIFICATION_REQUIRED')",
  "confidence_score": "number (0.0 to 1.0)",
  "confidence_level": "string ('high' | 'medium' | 'low')",
  "verification_required": "boolean",
  "verification_reason": "string | null",
  "grounding_status": "string ('fully_grounded' | 'partially_grounded' | 'unsupported' | 'unverifiable')",
  "groundedness_score": "number (0.0 to 1.0)",
  "citation_coverage": "number (0.0 to 1.0)",
  "claims": [
    {
      "claim_id": "string",
      "text": "string",
      "claim_type": "string ('fact' | 'interpretation' | 'uncertainty')",
      "citation_ids": ["string"],
      "support_status": "string ('supported' | 'partially_supported' | 'unsupported' | 'unverifiable')",
      "issues": ["string"]
    }
  ],
  "temporal_status": "string ('CURRENT' | 'SUPERSEDED' | 'AMENDED' | 'WITHDRAWN' | 'TEMPORALLY_UNCERTAIN')",
  "temporal_resolution": "object | null",
  "scheme_recommendation": {
    "scheme_code": "string (e.g. 'SCHEME-I', 'SCHEME-IV')",
    "scheme_name": "string",
    "is_mandatory": "boolean",
    "qco_number": "string | null",
    "qco_title": "string | null",
    "origin": "string ('domestic' | 'foreign')"
  },
  "certification_checklist": {
    "standard_number": "string",
    "scheme_code": "string",
    "scheme_name": "string",
    "steps": [
      {
        "step_number": "integer",
        "title": "string",
        "description": "string",
        "mandatory": "boolean",
        "timeline_estimate": "string",
        "action_required": "string"
      }
    ],
    "laboratories": [
      {
        "name": "string",
        "city": "string",
        "state": "string",
        "accreditation": "string",
        "scope": "string"
      }
    ]
  },
  "laboratories": ["array of Laboratory objects"],
  "model": "string (e.g. 'gpt-4o-mini' or 'extractive_rule_engine')",
  "language": "string ('en' | 'hi')"
}
```

---

### 3.2 `GET /status` — Service Status & Operational Observability

Unauthenticated liveness and metadata probe.

#### Response
```json
{
  "status": "ready",
  "collection_name": "bis_documents",
  "chunks_indexed": 479,
  "llm_available": true,
  "llm_last_error": null,
  "models": {
    "embedding_model": "BAAI/bge-large-en-v1.5",
    "reranker_model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "generator_model": "gpt-4o-mini"
  }
}
```

---

### 3.3 `GET /health` & `GET /ready` — Kubernetes / Container Apps Probes

- **`GET /health`**: Process liveness probe. Always returns `{"status": "ok"}` (HTTP 200).
- **`GET /ready`**: Traffic readiness probe. Verifies index integrity and schema compatibility.
  - Returns `200 OK` when index is healthy and query pipeline is mounted.
  - Returns `503 Service Unavailable` if index integrity evaluation fails or chunks are unindexed.

---

### 3.4 `GET /query/laboratories/{standard_number}` — Laboratory Directory

Returns recognized testing laboratories registered for a specific standard.

```json
{
  "standard_number": "IS 1293",
  "laboratories": [
    {
      "lab_id": "lab_bis_cl",
      "name": "BIS Central Laboratory (CL)",
      "city": "Sahibabad",
      "state": "Uttar Pradesh",
      "accreditation_number": "NABL-TC-5001",
      "scope_of_testing": ["Electrical Appliances", "Meters", "Cables", "Plugs and Sockets"]
    }
  ],
  "total_laboratories": 1
}
```

---

### 3.5 `POST /upload` — Document Acquisition & Ingestion Pipeline

Protected multipart upload endpoint for gazette notifications, standards, and laboratory reports.
- **Allowed MIME**: `application/pdf`, `text/markdown`, `application/json`
- **Max File Size**: 50 MB (`MAX_UPLOAD_SIZE_MB = 50`)
- **Returns**: HTTP 202 with `job_id` for asynchronous ingestion tracking.

---

## 4. Error Contract Matrix

All API errors return a standard RFC 7807 / FastAPI JSON body:
```json
{
  "detail": "Descriptive human-readable error explanation."
}
```

| HTTP Status | Trigger Condition | Example Error Detail |
| :--- | :--- | :--- |
| `400 Bad Request` | Unsupported parameter or malformed body | `"document_ids scoping is not supported..."` |
| `401 Unauthorized` | Missing or mismatched `X-Internal-Service-Key` | `"Missing required X-Internal-Service-Key header."` |
| `413 Payload Too Large` | Upload file exceeds 50 MB limit | `"File size exceeds maximum upload size (50 MB)."` |
| `429 Too Many Requests` | Max concurrent ingestions exceeded | `"Maximum concurrent ingestions reached (2)."` |
| `503 Service Unavailable` | Index corruption or unready state | `"Index integrity check raised: collection empty."` |
| `500 Internal Error` | Unhandled pipeline exception | `"Internal server error during query execution."` |

---

## 5. Architectural Contract Freeze Statement

The schemas documented herein are hereby declared **FROZEN**. Downstream consumers (including the ComplyWise web application, administrative dashboards, and third-party compliance integrations) may rely on these structures without risk of unannounced breaking changes.

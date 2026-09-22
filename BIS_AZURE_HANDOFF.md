# BIS Intelligence Engine — Azure Deployment & ComplyWise Handoff Specification

**Branch**: `V5`  
**Repository**: `Bis-system` (`https://github.com/Sadhitra-coder/Bis-system.git`)  
**Commit SHA**: `85ff263b65287bfeb11aa8e0eb48074d2fe9ae80`  
**Target Platform**: Microsoft Azure (Azure Container Apps / App Service Linux)  

---

## 1. Architectural Role & Boundary

The BIS Intelligence Engine runs as an **isolated backend microservice** providing grounded regulatory standard retrieval, temporal amendment resolution, and deterministic fact verification.

```
+---------------------------+
|    ComplyWise Frontend    | (Next.js / Vercel)
+-------------+-------------+
              | HTTP / REST (User Session / JWT)
              v
+-------------+-------------+
|    ComplyWise Backend     | (Django / Railway or Azure App Service)
+-------------+-------------+
              | HTTP / REST (Internal Service Key + Correlation ID)
              v
+-------------+-------------+
|    BIS Intelligence Engine | (FastAPI / Azure Container Apps)
+-------------+-------------+
       |             |
       v             v
+-------------+ +-------------+
|  ChromaDB   | | SQLite BIS  |
|  Vector DB  | |  Knowledge  |
+-------------+ +-------------+
```

---

## 2. Infrastructure & Compute Sizing

| Resource | Minimum Spec | Recommended Production Spec |
| :--- | :--- | :--- |
| **Hosting Mode** | Azure Container Apps (ACA) | Azure Container Apps (Dedicated or Consumption) |
| **vCPU** | 1.0 vCPU | 2.0 vCPU |
| **Memory** | 2.0 GiB | 4.0 GiB |
| **Storage** | 1.0 GiB persistent volume | Azure Files / Persistent Volume mount at `/app/data` |
| **OS / Runtime** | Debian Bookworm (Linux) | Python 3.11-slim |
| **Startup Time** | ~15-20 seconds (PyTorch cold load) | Health probe `initialDelaySeconds: 20` |

---

## 3. Container Specification (`Dockerfile`)

```dockerfile
FROM python:3.11-slim-bookworm

WORKDIR /app

# System dependencies for SQLite and compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY app/ app/
COPY data/ data/

# Expose internal service port
EXPOSE 8001

# Healthcheck
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -f http://127.0.0.1:8001/health || exit 1

# Launch uvicorn
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
```

---

## 4. Azure Environment Variables Configuration

| Variable Name | Example / Recommended Value | Description |
| :--- | :--- | :--- |
| `PROJECT_NAME` | `"BIS RAG Engine"` | Application service identifier |
| `APP_ENV` | `production` | Environment mode (`production` enforces auth) |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`INFO` or `DEBUG`) |
| `PORT` | `8001` | Listen port |
| `INTERNAL_SERVICE_KEY`| `sec_bis_azure_prod_99f482a...` | Shared secret key for internal ComplyWise calls |
| `LLM_ENABLED` | `false` (or `true`) | Controls generative answer synthesis |
| `GROQ_API_KEY` | `gsk_...` (optional) | Required only if `LLM_ENABLED=true` |
| `CHROMA_PERSIST_DIRECTORY`| `data/vector_db` | Path to persistent vector database |
| `KNOWLEDGE_DB_PATH` | `data/knowledge/bis_knowledge.db` | Path to persistent relational SQLite database |

---

## 5. Probes & Orchestration Health Monitoring

### 5.1 Liveness Probe (`GET /health`)
- **HTTP Path**: `/health`
- **Port**: `8001`
- **Initial Delay**: 15 seconds
- **Period**: 10 seconds
- **Expected Status**: `200 OK`
- **Payload**: `{"status": "ok"}`

### 5.2 Readiness Probe (`GET /ready`)
- **HTTP Path**: `/ready`
- **Port**: `8001`
- **Initial Delay**: 20 seconds
- **Period**: 15 seconds
- **Expected Status**: `200 OK` (or `503 Service Unavailable` if index integrity fails)
- **Payload**:
```json
{
  "status": "ready",
  "pipeline_ready": true,
  "index": {
    "state": "INDEX_READY",
    "healthy": true,
    "expected_schema_version": "6.0",
    "total_chunks": 1
  }
}
```

---

## 6. Service-to-Service Integration Contract (ComplyWise → BIS)

### 6.1 Request Endpoint
`POST http://<bis-service-host>:8001/query`

### 6.2 Request Headers
- `Content-Type: application/json`
- `X-Internal-Service-Key: <INTERNAL_SERVICE_KEY>`
- `X-Correlation-ID: complywise-<uuid>`

### 6.3 Request Body
```json
{
  "query": "What is the requirement under Section 14 sub-section (3) of the BIS Act?",
  "top_k": 3,
  "business_context": {
    "industry": "Jewellery Manufacturing",
    "scale": "MSME"
  }
}
```

### 6.4 Response Body
```json
{
  "query_id": "qry_883a9f12",
  "correlation_id": "complywise-uuid-1234",
  "query": "Section 14 sub-section (3)",
  "answer": "Qualified Answer (LLM generation disabled): Retrieved 1 relevant context passage(s)...",
  "sources": [
    {
      "chunk_id": "doc_90d46c947dc18a22_16d1dfa0e607f894",
      "citation": "sample_test.pdf, Clause 6053, p. 1",
      "similarity": 0.5508,
      "metadata": {
        "standard_number": "IS 3055:2024",
        "document_type": "gazette_notification",
        "authority": "BIS"
      }
    }
  ],
  "retrieved_chunks": 1,
  "decision": "qualified_answer",
  "query_state": "QUALIFIED_ANSWER",
  "confidence_score": 0.5508,
  "confidence_level": "MODERATE",
  "grounding_status": "fully_grounded",
  "verification_required": false
}
```

---

## 7. ComplyWise Backend Client Configuration (`ComplyWise/backend/.env`)

Add the following environment variables to `ComplyWise/backend`:

```env
# BIS Service Azure Connection
BIS_SERVICE_URL=http://127.0.0.1:8001       # Replace with Azure Container App FQDN in cloud
BIS_INTERNAL_SERVICE_KEY=complywise-internal-bis-key-default  # Replace with production secret
BIS_CLIENT_TIMEOUT=5.0
BIS_CLIENT_MAX_RETRIES=1
BIS_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3
BIS_CIRCUIT_BREAKER_RECOVERY_TIME=30.0
```

---

## 8. Rollback & Failover Strategy

If the BIS Engine is unreachable or degrades:
1. **Circuit Breaker Opens**: ComplyWise client trips after 3 consecutive failures.
2. **Safe Abstention Response**: ComplyWise serves an explainable status (`SERVICE_UNAVAILABLE` or `VERIFICATION_REQUIRED`) advising users to check official BIS portals (`manakonline.in`), with zero hallucinated compliance answers.
3. **No Database Poisoning**: Bis-system is read-only during query execution; tenant data in ComplyWise is completely unaffected.

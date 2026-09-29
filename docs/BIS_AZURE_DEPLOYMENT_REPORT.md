# BIS INTELLIGENCE — FINAL AZURE SYNC & DEPLOYMENT REPORT

## 1. Previous Deployed Commit
- **Commit SHA**: `b440ac0ca9f4a0a5015b6348ef52ea024f2b0561` (built in image `v22`, manifest updated in commit `37ad2f6`)
- **Container Tag**: `complywiseacr.azurecr.io/bis-system-v5:v22`

## 2. New MAIN Commit
- **Commit SHA**: `853d93beb49c69aa9f0c533ba665844903058f99`
- **Author**: Sadhitra-coder <sadhitramondal402@gmail.com>
- **Commit Date**: Tue Sep 29 08:25:29 2026 +0530
- **Message**: `error solved`
- **Synchronized Via**: Fast-forward merge (`git merge --ff-only origin/main`)

## 3. Azure Target
- **Target Type**: Azure Container Apps (ACA)
- **Container App (Backend)**: `bis-system-v5-korea`
- **Container App (Frontend)**: `bis-frontend-korea`
- **Resource Group**: `Storyvord-Test`
- **Location**: `koreacentral`
- **Managed Environment**: `complywise-env-korea`
- **Container Registry**: `complywiseacr.azurecr.io`

## 4. Deployment Method
- **Base Image**: `complywiseacr.azurecr.io/bis-system-v5:corpus-release-0001-789a18e001` (pre-cached offline HuggingFace embedding and reranker model weights)
- **Build Specification**: `Dockerfile.release` with synchronized `app/`, `requirements.txt`, `data/knowledge/bis_knowledge.db`, and `data/vector_db/`
- **New Image Tag**: `complywiseacr.azurecr.io/bis-system-v5:v23`
- **Image Digest**: `sha256:60299a8e4338fa384e801187b50b01ddc3d7e428da4bae69aec2e667c2f7c2fb`
- **Deployment Command**: `az containerapp update --name bis-system-v5-korea --resource-group Storyvord-Test --image complywiseacr.azurecr.io/bis-system-v5:v23`

## 5. Configuration Checks
All required environment variables verified on Azure Container App `bis-system-v5-korea`:
- `PORT`: Configured (8001)
- `HOST`: Configured (0.0.0.0)
- `LLM_ENABLED`: Configured (true)
- `OPENAI_MODEL`: Configured (gpt-4o-mini)
- `OPENAI_API_KEY`: Configured via Azure secret reference (`secretref:openai-api-key`)
- `INTERNAL_SERVICE_KEY`: Configured via Azure secret reference (`secretref:internal-service-key`)
- `TRANSFORMERS_NO_TF`: Configured (1)
- `USE_TF`: Configured (0)
- `SOURCE_COMMIT`: Configured (`853d93beb49c69aa9f0c533ba665844903058f99`)
- `RELEASE_ID`: Configured (`corpus-release-0002`)
- `IMAGE_DIGEST`: Configured (`sha256:60299a8e4338fa384e801187b50b01ddc3d7e428da4bae69aec2e667c2f7c2fb`)
- `BUILD_TIMESTAMP`: Configured (`2026-09-29T13:52:00Z`)
*(No secret values printed or exposed.)*

## 6. Migration Result
- **Database Architecture**: Embedded, immutable SQLite knowledge database (`data/knowledge/bis_knowledge.db`) and Chroma vector store (`data/vector_db/chroma.sqlite3`).
- **Migration Status**: Verified; zero schema migrations pending or required.
- **Data Integrity**: 479 indexed chunks across 19 standards, 25 catalog standards, 23 QCOs, 5 accredited laboratories, and 92 knowledge relationships verified intact. No destructive database commands executed.

## 7. Deployment Result
- **Provisioning State**: `Succeeded`
- **Running Status**: `Running`
- **Active Revision**: `bis-system-v5-korea--latest`
- **Revision Health**: `Healthy`
- **Replicas**: Active (1/1 provisioned)

## 8. Live URL
- **Backend API**: `https://bis-system-v5-korea.yellowmeadow-d3173c9a.koreacentral.azurecontainerapps.io`
- **Frontend UI & BFF**: `https://bis-frontend-korea.yellowmeadow-d3173c9a.koreacentral.azurecontainerapps.io`

## 9. Health-Check Result
- **`GET /health`**:
  - HTTP Status: `200 OK`
  - Response: `{"status":"ok"}`
- **`GET /status`**:
  - HTTP Status: `200 OK`
  - Response:
    ```json
    {
      "status": "ready",
      "collection_name": "bis_documents",
      "chunks_indexed": 479,
      "llm_available": true,
      "llm_last_error": null,
      "source_commit": "853d93beb49c69aa9f0c533ba665844903058f99",
      "release_id": "corpus-release-0002",
      "image_digest": "sha256:60299a8e4338fa384e801187b50b01ddc3d7e428da4bae69aec2e667c2f7c2fb",
      "build_timestamp": "2026-09-29T13:52:00Z",
      "models": {
        "embedding_model": "BAAI/bge-large-en-v1.5",
        "reranker_model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "generator_model": "gpt-4o-mini"
      }
    }
    ```

## 10. Manual Browser Verification
Full Playwright real Google Chrome acceptance battery executed directly against the live Azure deployment:
- **Test 1 — Landing Page**: PASSED (Brand heading, capability navigation links)
- **Test 2 — Ask BIS Live Query**: PASSED (Query *"What is the scope of IS 694?"* returned grounded answer with citations and evidence provenance)
- **Test 3 — Negative Control (IS 99999)**: PASSED (Honest abstention with verification-required state; 0 synthetic claims)
- **Test 4 — Standards Explorer**: PASSED (Search "cables", QCO mandatory toggle, Electrical category filtering)
- **Test 5 — Product Intelligence**: PASSED (Smart Electricity Meter preset ran 6-stage pipeline, identified IS 16444)
- **Test 6 — Certification Pathways**: PASSED (Scheme I, II, IV tabs switched, 5-stage roadmap rendered)
- **Test 7 — Laboratories & Google Maps**: PASSED (Accredited lab cards rendered verified physical address, official BIS source citation, and `[ Open in Google Maps ↗ ]` universal action)
- **Test 8 — Hallmarking & HUID Simulator**: PASSED (Purity grades 22K916, mandatory marks, HUID verification simulator)
- **Test 9 — Consumer Mode & Hindi**: PASSED (Plain-language mode toggled, Hindi prompt input rendered)
- **Test 10 — Compliance Passport**: PASSED (Dossier generated with SHA-256 integrity digest and print action)
- **Test 11 — Dashboard & Provenance**: PASSED (KPI cards rendered 479 indexed chunks, 23 QCOs, commit `853d93b` provenance)
- **Result**: 11 passed (50.4s), 0 failed, 0 flaky.

## 11. Any Deployment Fixes Made
- No code refactoring or application changes made.
- Synced latest code from remote `main` (`853d93b`).
- Used project's established `Dockerfile.release` with immutable offline HuggingFace model cache base image.
- Updated Azure Container App revision to point to newly built image `v23`.
- Updated container runtime environment variables (`SOURCE_COMMIT`, `IMAGE_DIGEST`, `BUILD_TIMESTAMP`).

## 12. Final Deployed Commit SHA
`853d93beb49c69aa9f0c533ba665844903058f99`

## 13. Final Git Status
```
On branch main
Your branch is up to date with 'origin/main'.

nothing to commit, working tree clean
```
*(Untouched: `ComplyWise` repository remains completely untouched.)*

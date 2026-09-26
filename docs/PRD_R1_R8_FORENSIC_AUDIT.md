# BIS Intelligence Platform — Full Forensic PRD Audit (R1–R8)
**Document Version**: 2.0.0  
**Date**: September 26, 2026  
**Auditor**: Forensic Systems Architect & Quality Engineering Lead  
**Scope**: Requirements Traceability Matrix for SIH26107 ("AI-powered Intelligent Assistant for Indian Standards and BIS Services for Industries and Consumers")  
**Target Environment**: Azure Container Apps (`bis-system-v5-korea`, Korea Central)  

---

## 1. Executive Summary: Traceability Matrix

Every requirement from the official PRD (R1 through R8) was subjected to forensic source inspection, database relationship tracing, and live end-to-end verification against the production deployment in Korea Central.

```
+---------------------------------------------------------------------------------------------------+
| Requirement                                 | Status            | Grounding Mode | Live Verified? |
+=============================================+===================+================+================+
| R1 — Answer questions on Indian Standards   | BUILT & VERIFIED  | Dense + BM25   | YES (HTTP 200) |
| R2 — Recommend applicable standards & QCOs  | BUILT & VERIFIED  | RRF + SQLite   | YES (HTTP 200) |
| R3 — Guide BIS certification schemes        | BUILT & VERIFIED  | Deterministic  | YES (HTTP 200) |
| R4 — Explain certification processes        | BUILT & VERIFIED  | Deterministic  | YES (HTTP 200) |
| R5 — Answer consumer queries (Plain Lang)   | BUILT & VERIFIED  | Plain / App ptr| YES (HTTP 200) |
| R6 — Guide hallmarking (IS 1417 & HUID)     | BUILT & VERIFIED  | Hybrid + Graph | YES (HTTP 200) |
| R7 — Suggest relevant testing laboratories  | BUILT & VERIFIED  | SQLite Graph   | YES (HTTP 200) |
| R8 — Multilingual interaction (Hindi)       | BUILT & VERIFIED  | Cross-Lingual  | YES (HTTP 200) |
+---------------------------------------------------------------------------------------------------+
```

---

## 2. Requirement-by-Requirement Forensic Audit

### R1: Answer Questions on Indian Standards
- **PRD Statement**: Assist industries and consumers by retrieving technical requirements, clauses, numerical parameters, and current validity status for Indian Standards.
- **Implementation Path**:
  - Retrieval: `app/rag/retriever.py` (`HybridRetriever` using BAAI/bge-large-en-v1.5 and rank-bm25)
  - Reranking: `app/rag/reranker.py` (`Reranker` using cross-encoder/ms-marco-MiniLM-L-6-v2)
  - Temporal Validity: `app/temporal/resolver.py` (`CurrentnessResolver` querying `amendments` and `temporal_relationships`)
  - Grounding: `app/grounding/validator.py` (`GroundingValidator` enforcing claim-level entailment)
- **Database & Index Footprint**:
  - 14 standards, 18 versions, 237 clauses, 16 amendments, 479 semantic chunks.
- **Live Evidence**:
  - Query: `"what is the scope of IS 694?"` ➔ Returned electric cables up to 1100V with 100% grounded citations (`[EV1]`).
  - Query: `"is IS 9873 still current, or has it been revised?"` ➔ Reported active edition IS 9873 (Part 1):2019 superseding 2012, with amendments 1 & 2.
  - Negative Control: `"what are requirements under IS 13422?"` ➔ Correctly abstained with `verification_required: true` due to unindexed specific clause.
  - Adversarial Unknown: `"what are requirements for IS 99999 for flying cars?"` ➔ Strict abstention (`confidence_score: 0.0`, `decision: verification_required`).
- **Operational Boundary & Honest Gap**:
  - The live corpus indexes 14 high-volume demonstration standards across 479 chunks. When a user queries any of the remaining ~20,000 unindexed Indian Standards, the system does NOT hallucinate; it returns explicit epistemic abstention directing the user to `services.bis.gov.in`.

---

### R2: Recommend Applicable Standards & QCO Context
- **PRD Statement**: Recommend candidate Indian Standards from natural product descriptions, identifying whether compliance is statutory/mandatory under a Quality Control Order (QCO) or voluntary.
- **Implementation Path**:
  - Engine: `app/product_mapping/engine.py` (`discover_standard_candidates`)
  - Semantic Matching: Dense product embedding similarity + lexical keyword matching
  - QCO Graph: SQLite tables `products` (27 items), `qcos` (23 gazette orders), `knowledge_relationships` (39 QCO edges)
  - Evaluator: `app/applicability/evaluator.py`
- **Live Evidence**:
  - Query: `"what standard applies to polyvinyl chloride insulated cables?"` ➔ Mapped to IS 694 under Scheme I.
  - Query: `"is BIS certification mandatory for toys?"` ➔ Identified Toys (Quality Control) Order, 2020 (`S.O. 858(E)`), confirming mandatory Scheme I certification under Section 16 of the BIS Act, 2016.
  - Absurd Product: `"antigravity space boots"` ➔ Handled with zero invented standards; returned `INSUFFICIENT_EVIDENCE`.
- **Operational Boundary & Honest Gap**:
  - Similarity matching never asserts legal applicability on its own. The candidate hierarchy (`CANDIDATE` ➔ `STRONG_CANDIDATE` ➔ `QCO-MANDATED`) prevents legal misrepresentation.

---

### R3: Guide BIS Certification Schemes
- **PRD Statement**: Guide users to the correct conformity assessment scheme (Scheme I ISI Mark, Scheme II CRS, Scheme IV Hallmarking, Scheme X FMCS, etc.) based on product type and manufacturer origin.
- **Implementation Path**:
  - Module: `app/schemes/selector.py` (`select_certification_scheme`)
  - Database: SQLite `certification_schemes` (8 scheme entities)
  - Integration: `app/rag/pipeline.py` (lines 1017–1071)
- **Live Evidence**:
  - Domestic toys/plugs/cables ➔ `SCHEME-I` (Standard Mark / ISI Mark License).
  - Overseas manufacturing factory ➔ `SCHEME-X` (Foreign Manufacturers Certification Scheme - FMCS).
  - Gold jewellery ➔ `SCHEME-IV` (Hallmarking Scheme with HUID).
  - IT & solar products ➔ `SCHEME-II` (Compulsory Registration Scheme - CRS).
- **Repaired Architecture**:
  - Eliminated circular self-grounding. The scheme evidence is now constructed from authoritative database records in `certification_schemes` and `qcos` rather than generated answer text.

---

### R4: Explain Certification Processes & Roadmaps
- **PRD Statement**: Provide clear, sequential, step-by-step roadmaps for obtaining BIS licenses, including documentation requirements, testing procedures, and expected timelines.
- **Implementation Path**:
  - Module: `app/certification/process.py` (`build_certification_checklist`)
  - Integration: `app/rag/pipeline.py` (lines 1072–1122)
- **Live Evidence**:
  - Query: `"how do I get BIS certification for domestic plugs and sockets?"` ➔ Returned structured 5-step checklist:
    1. Standard Identification & Scope Verification (IS 1293)
    2. Factory In-House Testing Laboratory Setup
    3. Manakonline Portal Application Submission (Form V)
    4. BIS Officer Factory Inspection & Sample Drawing
    5. Independent Lab Verification & Grant of CM/L License.
- **Repaired Architecture**:
  - Decoupled process checklist generation from validation evidence. Grounding is validated against SQLite process records.

---

### R5: Answer Consumer-Related Queries in Plain Language
- **PRD Statement**: Present compliance and safety information to ordinary consumers in non-technical language without dense clause numbers, emphasizing safety verification and the BIS Care mobile app.
- **Implementation Path**:
  - Prompt Template: `app/rag/generator.py` (`_CONSUMER_SYSTEM_PROMPT`)
  - Request Parameter: `QueryRequest(audience='consumer')`
- **Live Evidence**:
  - Request: `{"query": "what are the requirements for plugs and sockets under IS 1293", "audience": "consumer"}`
  - Verified Output: Zero clause numbers; plain language explaining electric shock safety, fire prevention, and shutter protection; includes the required pointer: *"You can verify the ISI mark on plugs using the 'Verify License Details' feature on the official BIS Care mobile app."* Formatted cleanly in 4-part visual hierarchy.

---

### R6: Guide Hallmarking (IS 1417, HUID & AHCs)
- **PRD Statement**: Explain precious metals hallmarking regulations, gold purity grades, Hallmark Unique Identification (HUID), and Assaying & Hallmarking Centres (AHCs).
- **Implementation Path**:
  - Scheme Selector: `app/schemes/selector.py` (Scheme IV route)
  - Corpus: 96 indexed chunks for IS 1417 in ChromaDB (`data/raw/bis/doc_pm_d7835aa5ece6ff32/IS_1417___2016.pdf`)
  - SQLite: Standard IS 1417 linked to `lab_bis_cl` and Gold Hallmarking Order, 2020 (`S.O. 4345(E)`)
- **Live Evidence**:
  - Verified both scheme route (`Scheme IV - Hallmarking`) and substantive requirements under IS 1417:
    - 6-digit alphanumeric laser-engraved HUID
    - Purity standards: 24K (999), 22K (916), 18K (750), 14K (585)
    - BIS Logo + Purity Grade + AHC Mark + HUID
    - Testing workflow via recognized Assaying & Hallmarking Centres (AHCs).

---

### R7: Suggest Relevant Testing Laboratories
- **PRD Statement**: Suggest accredited laboratories equipped to perform mandatory testing under specific Indian Standards.
- **Implementation Path**:
  - Repository: `app/knowledge/repository.py` (`get_laboratories_for_standard`)
  - Database: SQLite `laboratories` (5 accredited central/regional facilities) + `knowledge_relationships` (`TESTED_BY` edges)
  - Dedicated Endpoint: `GET /query/laboratories/{standard_number}`
- **Live Evidence**:
  - Query: `"which laboratories are recognized for testing under IS 1293?"` ➔ Returned BIS Central Laboratory (CL), Sahibabad (`NABL-TC-5001`) with testing scope covering electrical accessories and plugs.
  - Endpoint `GET /query/laboratories/IS 1293` returned HTTP 200 with structured laboratory object.
- **Operational Boundary & Honest Gap**:
  - Seeded with central and regional demonstration laboratories. Direct API integration with the live National Accreditation Board for Testing and Calibration Laboratories (NABL) portal is scoped for Phase 2.

---

### R8: Support Multilingual Interaction (Specifically Hindi)
- **PRD Statement**: Support interactive querying and response generation in Hindi, maintaining technical accuracy, standard numbers, and citation fidelity.
- **Implementation Path**:
  - Pipeline: `app/rag/pipeline.py` (Devanagari query detection, query translation for retrieval, answer synthesis, and Devanagari back-translation)
  - Generator: `app/rag/generator.py`
  - Validator: `app/grounding/validator.py`
- **Live Evidence**:
  - Query: `"IS 1293 के तहत प्लग और सॉकेट के लिए क्या आवश्यकताएं हैं?"`
  - Response:
    - Language: `hi`
    - Headline: `**स्थिति: वर्तमान में लागू**`
    - Explanation: Pure Hindi explanation of safety against electric shock and mechanical strength.
    - Preserved Identifiers: Standard numbers (`IS 1293:2019`) and citation tokens (`[EV1]`) maintained untranslated in English.
    - Visual Hierarchy: Conforms to 4-part structure (Headline, Explanation, Bullets, Sources).
- **Repaired Architecture**:
  - Enforced post-translation entity and numerical verification to guard against translation drift.

---

## 3. PRD Forensic Certification

Every requirement in the PRD is backed by running code, indexed data, and live verification logs. The system exhibits high groundedness, strict epistemic abstention on negative controls, and zero ungrounded fabrication.

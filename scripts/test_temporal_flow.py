"""
scripts/test_temporal_flow.py

Local verification of temporal resolution and pipeline behavior for:
1. Populated demo standard: IS 9873
2. Populated demo standard: IS 1293
3. Unpopulated negative control: IS 13422
"""

import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.pipeline import RAGPipeline
from app.knowledge.repository import KnowledgeRepository

def test_flow():
    repo = KnowledgeRepository()
    pipeline = RAGPipeline(knowledge_repo=repo)

    test_queries = [
        "is IS 9873 still current, or has it been revised?",
        "is IS 1293 still current, or has it been revised?",
        "is IS 13422 still current, or has it been revised?",
    ]

    for q in test_queries:
        print("\n" + "=" * 70)
        print(f"QUERY: {q}")
        print("=" * 70)
        resp = pipeline.query(q)
        print(f"Decision: {resp.get('decision')}")
        print(f"Verification Required: {resp.get('verification_required')}")
        print(f"Verification Reason: {resp.get('verification_reason')}")
        print(f"Temporal Status: {resp.get('temporal_status')}")
        print(f"Temporal Verification Required: {resp.get('temporal_verification_required')}")
        print(f"Grounding Status: {resp.get('grounding_status')}")
        print(f"Grounding Reason: {resp.get('grounding_reason')}")
        print("\nANSWER PREVIEW:")
        ans = resp.get("answer", "")
        print(ans[:400] + ("..." if len(ans) > 400 else ""))

if __name__ == "__main__":
    test_flow()

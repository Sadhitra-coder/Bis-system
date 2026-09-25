"""
Verification script for:
- Workstream 1: Answer Formatting (4-part visual hierarchy)
- Part A (R3): Certification Scheme Guidance
- Part B (R4): Certification Process Checklist
"""
import sys
import json
import logging
from pathlib import Path

# Setup path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Quiet down verbose logging
logging.basicConfig(level=logging.WARNING)

from app.rag.pipeline import RAGPipeline

def run_tests():
    print("=" * 80)
    print("INITIALIZING RAG PIPELINE FOR LOCAL VERIFICATION...")
    print("=" * 80)
    pipeline = RAGPipeline()

    test_queries = [
        ("WS1 - Test 1: IS 9873 Version Status", "is IS 9873 still current, or has it been revised?", {}),
        ("WS1 - Test 2: IS 1293 Version Status", "is IS 1293 still current, or has it been revised?", {}),
        ("WS1 - Test 3: IS 13422 Negative Control", "what are the requirements under IS 13422?", {}),
        ("Part A - R3 Test 1: IS 9873 Mandatory Scheme (Domestic)", "which certification scheme applies for toys under IS 9873", {"manufacturer_origin": "domestic"}),
        ("Part A - R3 Test 2: IS 1417 Hallmarking Scheme", "what is the certification scheme for gold jewellery under IS 1417", {"manufacturer_origin": "domestic"}),
        ("Part A - R3 Test 3: IS 3055 Scheme Guidance", "which certification scheme applies to IS 3055", {"manufacturer_origin": "domestic"}),
        ("Part B - R4 Test 1: IS 9873 Certification Process Checklist", "how do I get certification for toys under IS 9873", {}),
    ]

    for label, query, kwargs in test_queries:
        print("\n" + "=" * 80)
        print(f"RUNNING: {label}")
        print(f"QUERY: {query}")
        print("=" * 80)

        res = pipeline.query(query, **kwargs)

        print(f"DECISION: {res.get('decision')}")
        print(f"CONFIDENCE: {res.get('confidence_score')} ({res.get('confidence_level')})")
        print(f"INTENT: {res.get('intent')}")
        print(f"VERIFICATION REQUIRED: {res.get('verification_required')}")
        if res.get('verification_reason'):
            print(f"VERIFICATION REASON: {res.get('verification_reason')}")
        print(f"GROUNDING STATUS: {res.get('grounding_status')}")
        print("\nFORMATTED ANSWER:\n")
        print(res.get("answer"))
        print("\n" + "-" * 80)

        if res.get("scheme_recommendation"):
            print(f"SCHEME RECOMMENDATION: {res['scheme_recommendation'].get('scheme_code')} ({res['scheme_recommendation'].get('scheme_name')})")
        if res.get("certification_checklist"):
            print(f"CERTIFICATION CHECKLIST: {len(res['certification_checklist'].get('steps', []))} steps | Labs: {len(res['certification_checklist'].get('laboratories', []))}")

if __name__ == "__main__":
    run_tests()

import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

# Set environment
os.environ["PYTHONPATH"] = "E:\\ComplienceManagement\\Bis-system"
os.environ["TRANSFORMERS_NO_TF"] = "1"
os.environ["USE_TF"] = "0"
os.environ["MPLCONFIGDIR"] = os.environ.get("TEMP", "C:\\Temp")

from app.rag.pipeline import RAGPipeline

pipeline = RAGPipeline()

print("=" * 80)
print("TEST 1: ISSUE A - BROAD QUERIES CONFIDENCE & GROUNDING")
print("=" * 80)

issue_a_queries = [
    ("IS 16444 (Smart Meters)", "what are the requirements for smart meters under IS 16444"),
    ("IS 1417 (Gold Hallmarking)", "what are the requirements for gold hallmarking under IS 1417"),
    ("IS 1293 (Plugs and Sockets)", "what are the requirements for plugs and sockets under IS 1293"),
    ("IS 13422 (Surgical Gloves)", "what are the requirements for surgical gloves under IS 13422"),
    ("IS 9283 (Submersible Pump Motors)", "what are the requirements for submersible pump motors under IS 9283"),
    ("IS 694 (PVC Cables)", "what are the requirements for pvc cables under IS 694"),
    ("IS 1786 (TMT Rebars)", "what are the requirements for tmt rebars under IS 1786"),
]

for label, q in issue_a_queries:
    print(f"\n--- Testing: {label} ---")
    res = pipeline.query(q)
    print(f"Query: {q}")
    print(f"Decision: {res.get('decision')} | Conf: {res.get('confidence_score')} ({res.get('confidence_level')})")
    print(f"Verification Required: {res.get('verification_required')} | Reason: {res.get('verification_reason')}")
    print(f"Grounding Status: {res.get('grounding_status')} | Score: {res.get('groundedness_score')}")
    print(f"Laboratories count: {len(res.get('laboratories') or [])}")
    print(f"Answer Preview:\n{res.get('answer', '')[:300]}...")

print("\n" + "=" * 80)
print("TEST 2: NEGATIVE CONTROLS (MUST STRICTLY ABSTAIN)")
print("=" * 80)

neg_queries = [
    ("Flying cars IS 99999", "what are the requirements for flying cars under IS 99999"),
    ("A thing that does stuff", "a thing that does stuff"),
]

for label, q in neg_queries:
    print(f"\n--- Testing Negative Control: {label} ---")
    res = pipeline.query(q)
    print(f"Decision: {res.get('decision')}")
    print(f"Verification Required: {res.get('verification_required')} (Expected: True)")
    print(f"Verification Reason: {res.get('verification_reason')}")

print("\n" + "=" * 80)
print("TEST 3: CONSUMER MODE (AUDIENCE='CONSUMER')")
print("=" * 80)

consumer_q = "what are the requirements for plugs and sockets under IS 1293"
res_consumer = pipeline.query(consumer_q, audience="consumer")
print(f"Decision: {res_consumer.get('decision')}")
print(f"Grounding: {res_consumer.get('grounding_status')}")
print(f"Answer (Consumer):\n{res_consumer.get('answer')}")

print("\n" + "=" * 80)
print("TEST 4: HINDI QUERY SUPPORT")
print("=" * 80)

hindi_q = "IS 1293 के तहत प्लग और सॉकेट के लिए क्या आवश्यकताएं हैं?"
res_hindi = pipeline.query(hindi_q)
print(f"Original Query: {res_hindi.get('query')}")
print(f"Language: {res_hindi.get('language')}")
print(f"Decision: {res_hindi.get('decision')}")
print(f"Answer (Hindi):\n{res_hindi.get('answer')}")

print("\n" + "=" * 80)
print("TEST 5: TESTING LABORATORIES ENDPOINT/REPO CHECK")
print("=" * 80)

from app.knowledge.repository import default_repository
for std in ["IS 1293", "IS 694", "IS 9873", "IS 3055", "IS 16444", "IS 1786", "IS 1417"]:
    labs = default_repository.get_laboratories_for_standard(std)
    print(f"Standard: {std:10} -> {len(labs)} laboratory/ies: {[l.name for l in labs]}")

print("\nTEST SUITE COMPLETED SUCCESSFULLY.")

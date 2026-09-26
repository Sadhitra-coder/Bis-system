"""
scripts/verify_live_deployment.py

Live HTTP verification against Azure Container App: bis-system-v5-korea
Endpoint: https://bis-system-v5-korea.yellowmeadow-d3173c9a.koreacentral.azurecontainerapps.io/query
"""

import json
import os
import urllib.request
import urllib.error

ENDPOINT = "https://bis-system-v5-korea.yellowmeadow-d3173c9a.koreacentral.azurecontainerapps.io/query"
# Key must be supplied via environment variable; do not hardcode secrets in source or scripts
API_KEY = os.environ.get("INTERNAL_SERVICE_KEY")
if not API_KEY:
    raise ValueError(
        "INTERNAL_SERVICE_KEY environment variable is required to run verification. "
        "Please set INTERNAL_SERVICE_KEY before executing."
    )

TEST_CASES = [
    ("WS1 - Test 1: IS 9873 Version Status", {"query": "is IS 9873 still current, or has it been revised?", "audience": "technical"}),
    ("WS1 - Test 2: IS 1293 Version Status", {"query": "is IS 1293 still current, or has it been revised?", "audience": "technical"}),
    ("WS1 - Test 3: IS 13422 Negative Control", {"query": "what are the requirements under IS 13422?", "audience": "technical"}),
    ("Part A - R3 Test 1: IS 9873 Mandatory Scheme (Domestic)", {"query": "which certification scheme applies for toys under IS 9873", "manufacturer_origin": "domestic"}),
    ("Part A - R3 Test 2: IS 1417 Hallmarking Scheme", {"query": "what is the certification scheme for gold jewellery under IS 1417", "manufacturer_origin": "domestic"}),
    ("Part B - R4 Test 1: IS 9873 Certification Process Checklist", {"query": "how do I get certification for toys under IS 9873"}),
]

def run_verification():
    results = []
    print("=" * 80)
    print("LIVE AZURE ENDPOINT VERIFICATION")
    print(f"Target: {ENDPOINT}")
    print("=" * 80)

    for label, payload_dict in TEST_CASES:
        print("\n" + "=" * 80)
        print(f"TEST: {label}")
        print(f"QUERY: {payload_dict['query']}")
        print("=" * 80)

        payload = json.dumps(payload_dict).encode("utf-8")
        req = urllib.request.Request(
            ENDPOINT,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Internal-Service-Key": API_KEY,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                status_code = resp.getcode()
                body = json.loads(resp.read().decode("utf-8"))
                print(f"HTTP Status: {status_code}")
                print(f"Decision: {body.get('decision')}")
                print(f"Confidence: {body.get('confidence_score')} ({body.get('confidence_level')})")
                print(f"Intent: {body.get('intent')}")
                print(f"Verification Required: {body.get('verification_required')}")
                if body.get('verification_reason'):
                    print(f"Verification Reason: {body.get('verification_reason')}")
                print(f"Grounding Status: {body.get('grounding_status')}")

                if body.get('scheme_recommendation'):
                    s = body['scheme_recommendation']
                    print(f"Scheme Recommendation: {s.get('scheme_code')} ({s.get('scheme_name')}) | Mandatory: {s.get('is_mandatory')}")

                if body.get('certification_checklist'):
                    c = body['certification_checklist']
                    print(f"Certification Checklist: {len(c.get('steps', []))} steps | Recognized Labs: {len(c.get('laboratories', []))}")

                print("\nFORMATTED ANSWER:\n")
                print(body.get("answer"))
                print("\n" + "-" * 80)
                results.append({"label": label, "status": status_code, "body": body})
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            print(f"HTTP Error {e.code}: {err_body}")
            results.append({"label": label, "error": f"HTTP {e.code}: {err_body}"})
        except Exception as e:
            print(f"Error: {e}")
            results.append({"label": label, "error": str(e)})

    # Save verification output to file
    with open("live_verification_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    run_verification()

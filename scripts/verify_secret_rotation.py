"""
scripts/verify_secret_rotation.py

Tests that:
1. Old credential is rejected (403 Forbidden)
2. No credential is rejected (403 Forbidden)
3. New rotated credential is accepted (200 OK)

Zero credential printing.
"""

import json
import urllib.request
import urllib.error

import os

ENDPOINT = "https://bis-system-v5-korea.yellowmeadow-d3173c9a.koreacentral.azurecontainerapps.io/query"
OLD_KEY = os.getenv("OLD_REVOKED_INTERNAL_KEY", "REVOKED_OLD_KEY_PLACEHOLDER")


def test_key(key: str | None, label: str):
    data = json.dumps({"query": "what is the scope of IS 694?", "top_k": 2}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-Internal-Service-Key"] = key

    req = urllib.request.Request(ENDPOINT, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            body = resp.read().decode("utf-8")
            print(f"[{label}] -> HTTP {status} (SUCCESS)")
            return status, body
    except urllib.error.HTTPError as e:
        print(f"[{label}] -> HTTP {e.code} ({e.reason})")
        return e.code, e.read().decode("utf-8")
    except Exception as e:
        print(f"[{label}] -> Exception: {e}")
        return 500, str(e)


def main():
    print("Testing security boundaries on live Azure deployment...")

    # 1. No credential -> 401 Unauthorized
    status_no_key, _ = test_key(None, "NO_CREDENTIAL")
    assert status_no_key == 401, f"Expected 401 for NO_CREDENTIAL, got {status_no_key}"

    # 2. Old revoked credential -> 401 Unauthorized
    status_old_key, _ = test_key(OLD_KEY, "OLD_REVOKED_CREDENTIAL")
    assert status_old_key == 401, f"Expected 401 for OLD_REVOKED_CREDENTIAL, got {status_old_key}"

    # 3. New rotated credential -> 200 OK
    with open("scratch/.new_internal_key", "r") as f:
        new_key = f.read().strip()

    status_new_key, body = test_key(new_key, "NEW_ROTATED_CREDENTIAL")
    assert status_new_key == 200, f"Expected 200 for NEW_ROTATED_CREDENTIAL, got {status_new_key}"

    print("\nALL 3 SECURITY BOUNDARY CHECKS PASSED:")
    print("[PASS] Missing credential rejected with HTTP 401")
    print("[PASS] Old revoked credential rejected with HTTP 401")
    print("[PASS] New rotated credential accepted with HTTP 200")


if __name__ == "__main__":
    main()

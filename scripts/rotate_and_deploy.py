"""
scripts/rotate_and_deploy.py

Rotates the internal service key and deploys the new immutable image to ACA.
Ensures zero credential leakage to console or logs.
"""

import json
import secrets
import subprocess
from datetime import datetime, timezone

RESOURCE_GROUP = "Storyvord-Test"
APP_NAME = "bis-system-v5-korea"
NEW_IMAGE = "complywiseacr.azurecr.io/bis-system-v5:v22"
SOURCE_COMMIT = "b440ac0ca9f4a0a5015b6348ef52ea024f2b0561"
IMAGE_DIGEST = "sha256:141c4d32316a7fb5b8a84d3768d0a1edd6c639de9ec045ae83d5d804cc3bf8d2"
RELEASE_ID = "corpus-release-0002"
BUILD_TIMESTAMP = "2026-09-26T17:06:38Z"


def main():
    print("Generating new secure internal service key...")
    new_key = secrets.token_urlsafe(32)

    # Save to gitignored scratch file for test runner access
    with open("scratch/.new_internal_key", "w") as f:
        f.write(new_key)
    print("New key generated and saved locally to scratch/.new_internal_key (gitignored).")

    print(f"Updating secret 'internal-service-key' in Azure Container App '{APP_NAME}'...")
    res = subprocess.run([
        "az", "containerapp", "secret", "set",
        "--name", APP_NAME,
        "--resource-group", RESOURCE_GROUP,
        "--secrets", f"internal-service-key={new_key}"
    ], capture_output=True, text=True, shell=True)
    if res.returncode != 0:
        print(f"Secret update failed: {res.stderr}")
        return

    print("Secret rotated successfully in ACA.")

    print(f"Updating Container App '{APP_NAME}' to image '{NEW_IMAGE}'...")
    res = subprocess.run([
        "az", "containerapp", "update",
        "--name", APP_NAME,
        "--resource-group", RESOURCE_GROUP,
        "--image", NEW_IMAGE,
        "--set-env-vars",
        f"SOURCE_COMMIT={SOURCE_COMMIT}",
        f"RELEASE_ID={RELEASE_ID}",
        f"IMAGE_DIGEST={IMAGE_DIGEST}",
        f"BUILD_TIMESTAMP={BUILD_TIMESTAMP}",
        "INTERNAL_SERVICE_KEY=secretref:internal-service-key"
    ], capture_output=True, text=True, shell=True)

    if res.returncode != 0:
        print(f"Container App update failed: {res.stderr}")
        return

    print("Container App update command succeeded. Inspecting new revision...")
    res = subprocess.run([
        "az", "containerapp", "show",
        "--name", APP_NAME,
        "--resource-group", RESOURCE_GROUP,
        "--query", "{latestRevisionName:properties.latestRevisionName, fqdn:properties.configuration.ingress.fqdn, runningStatus:properties.runningStatus}",
        "-o", "json"
    ], capture_output=True, text=True, shell=True)

    info = json.loads(res.stdout)
    print("Deployment info:", json.dumps(info, indent=2))

    record = {
        "source_commit": SOURCE_COMMIT,
        "image_tag": "v20",
        "image_digest": IMAGE_DIGEST,
        "aca_revision": info.get("latestRevisionName"),
        "deployment_timestamp": datetime.now(timezone.utc).isoformat(),
        "corpus_release": RELEASE_ID,
        "fqdn": info.get("fqdn")
    }
    with open("data/releases/corpus-release-0002/deployment.json", "w") as f:
        json.dump(record, f, indent=2)
    print("Deployment record written to data/releases/corpus-release-0002/deployment.json")


if __name__ == "__main__":
    main()

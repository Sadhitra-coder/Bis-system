"""app/release/publish.py

CLI entrypoint for:
    python -m app.release.publish --release <release_id>
"""

import argparse
import logging
import sys

from app.release.publisher import publish_release

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main():
    parser = argparse.ArgumentParser(
        description="Publish an immutable BIS Corpus Release to Azure Container Apps"
    )
    parser.add_argument(
        "--release",
        type=str,
        required=True,
        help="Release ID to publish (e.g. 'corpus-release-0001')",
    )
    parser.add_argument(
        "--acr-name",
        type=str,
        default="complywiseacr",
        help="Azure Container Registry name (default: complywiseacr)",
    )
    parser.add_argument(
        "--resource-group",
        type=str,
        default="Storyvord-Test",
        help="Azure Resource Group (default: Storyvord-Test)",
    )
    parser.add_argument(
        "--container-app",
        type=str,
        default="bis-system-v5",
        help="Azure Container App name (default: bis-system-v5)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate manifest, index integrity, and hashes without building or deploying",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip docker build step (assume image already built)",
    )
    parser.add_argument(
        "--skip-push",
        action="store_true",
        help="Skip docker push step (assume image already in ACR)",
    )
    parser.add_argument(
        "--skip-deploy",
        action="store_true",
        help="Skip Azure containerapp update step",
    )

    args = parser.parse_args()

    try:
        report = publish_release(
            release_id=args.release,
            acr_name=args.acr_name,
            resource_group=args.resource_group,
            container_app=args.container_app,
            dry_run=args.dry_run,
            skip_build=args.skip_build,
            skip_push=args.skip_push,
            skip_deploy=args.skip_deploy,
        )
        print("\n============================================================")
        print("PUBLISH SUMMARY")
        print("============================================================")
        print(f"Release ID:  {report['release_id']}")
        print(f"Status:      {report['status']}")
        if "image_tag" in report:
            print(f"Image Tag:   {report['image_tag']}")
        if "azure_deployment" in report.get("steps", {}):
            az = report["steps"]["azure_deployment"]
            print(f"Azure State: {az.get('provisioning_state')}")
            print(f"Revision:    {az.get('latest_revision')}")
            print(f"FQDN:        {az.get('fqdn')}")
        print("============================================================\n")
    except Exception as exc:
        logging.error("Publish execution failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

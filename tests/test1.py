from app.config import (
    settings,
    create_required_directories,
)

from app.models import (
    DocumentCreate,
    DocumentStatus,
)


def main():
    print("\n--- CONFIG TEST ---\n")

    create_required_directories()

    print("Environment:")
    print(settings.APP_ENV)

    print("\nDirectories:")

    print("Raw:")
    print(settings.RAW_DATA_DIR)

    print("\nMarkdown:")
    print(settings.MARKDOWN_DIR)

    print("\nStructured:")
    print(settings.STRUCTURED_DIR)

    print("\n--- MODEL TEST ---\n")

    document = DocumentCreate(
        document_id="test-001",
        filename="clinical_thermometer.pdf",
    )

    status = DocumentStatus(
        document_id=document.document_id,
        status="created",
        current_step="initialization",
    )

    print(document.model_dump())

    print(status.model_dump())

    print("\n✓ PHASE 1 WORKING\n")


if __name__ == "__main__":
    main()
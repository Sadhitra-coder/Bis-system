from __future__ import annotations

from app.crawler.metadata import extract_metadata
from app.database.models import Document
from app.database.session import SessionLocal


BATCH_SIZE = 250


def main():

    db = SessionLocal()

    try:

        total = (
            db.query(Document)
            .count()
        )

        print(
            f"Total documents: {total}"
        )

        processed = 0

        while processed < total:

            documents = (
                db.query(Document)
                .order_by(Document.id)
                .offset(processed)
                .limit(BATCH_SIZE)
                .all()
            )

            if not documents:
                break

            for doc in documents:

                filename = None

                if doc.document_url:

                    filename = (
                        doc.document_url
                        .split("?")[0]
                        .rsplit("/", 1)[-1]
                    )

                metadata = extract_metadata(
                    title=doc.title,
                    url=doc.document_url,
                    description=doc.description,
                    filename=filename,
                )

                doc.title = metadata["title"]

                doc.document_type = (
                    metadata["document_type"]
                )

                doc.category = (
                    metadata["category"]
                )

                doc.subcategory = (
                    metadata["subcategory"]
                )

                doc.standard_number = (
                    metadata["standard_number"]
                )

                doc.revision = (
                    metadata["revision"]
                )

                doc.edition = (
                    metadata["edition"]
                )

                doc.keywords = (
                    metadata["keywords"]
                )

            db.commit()

            processed += len(documents)

            print(
                f"Processed "
                f"{processed}/{total}"
            )

        print(
            "\nMetadata refresh completed."
        )

    except Exception:

        db.rollback()
        raise

    finally:

        db.close()


if __name__ == "__main__":
    main()
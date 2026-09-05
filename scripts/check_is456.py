from app.database.models import Document
from app.database.session import SessionLocal


def main():

    db = SessionLocal()

    try:

        # ====================================================
        # STANDARD NUMBER
        # ====================================================

        print()
        print("=" * 80)
        print("STANDARD_NUMBER MATCH")
        print("=" * 80)

        results = (
            db.query(Document)
            .filter(
                Document.standard_number.ilike(
                    "IS 456"
                )
            )
            .all()
        )

        print(
            f"Exact matches: {len(results)}"
        )

        for doc in results:

            print(
                f"[{doc.id}] "
                f"{doc.title} | "
                f"STD={doc.standard_number} | "
                f"TYPE={doc.document_type}"
            )

        # ====================================================
        # TITLE
        # ====================================================

        print()
        print("=" * 80)
        print("TITLE MATCH")
        print("=" * 80)

        results = (
            db.query(Document)
            .filter(
                Document.title.ilike(
                    "%IS 456%"
                )
            )
            .all()
        )

        print(
            f"Title matches: {len(results)}"
        )

        for doc in results:

            print(
                f"[{doc.id}] "
                f"{doc.title} | "
                f"STD={doc.standard_number} | "
                f"TYPE={doc.document_type}"
            )

        # ====================================================
        # URL
        # ====================================================

        print()
        print("=" * 80)
        print("URL MATCH")
        print("=" * 80)

        results = (
            db.query(Document)
            .filter(
                Document.document_url.ilike(
                    "%IS-456%"
                )
            )
            .all()
        )

        print(
            f"URL matches: {len(results)}"
        )

        for doc in results:

            print(
                f"[{doc.id}] "
                f"{doc.title} | "
                f"STD={doc.standard_number} | "
                f"{doc.document_url}"
            )

    finally:

        db.close()


if __name__ == "__main__":
    main()
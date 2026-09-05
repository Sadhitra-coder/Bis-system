from app.database.session import SessionLocal
from app.database.repositories.document import DocumentRepository


def main():
    db = SessionLocal()

    try:
        repo = DocumentRepository(db)

        print("Total documents:", repo.count_documents())
        print("Unparsed:", repo.count_unparsed())
        print("Embedded:", repo.count_embedded())

        results = repo.search(
            query="laboratory",
            limit=10,
        )

        print("\nSearch results:")

        for doc in results:
            print(
                f"[{doc.id}] "
                f"{doc.title} | "
                f"{doc.document_type} | "
                f"{doc.standard_number}"
            )

    finally:
        db.close()


if __name__ == "__main__":
    main()
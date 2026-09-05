from app.crawler.metadata import extract_metadata


TEST_CASES = [

    {
        "title":
            "IS 456:2000 Plain and Reinforced Concrete",

        "url":
            "https://example.com/IS-456-2000.pdf",
    },

    {
        "title":
            "IS 10262 Concrete Mix Proportioning",

        "url":
            "https://example.com/IS-10262.pdf",
    },

    {
        "title":
            "Cement Testing Guidelines",

        "url":
            "https://example.com/cement-testing-guidelines.pdf",
    },

    {
        "title":
            "Cement Capsule Course",

        "url":
            "https://example.com/cement-course.pdf",
    },

    {
        "title":
            "Revised Guidelines for support to other laboratories dated 2024 12 17",

        "url":
            "https://example.com/"
            "Revised-Guidelines-for-support-to-other-laboratories-dated-2024-12-17.pdf",
    },

    {
        "title":
            "Act Enforcement",

        "url":
            "https://example.com/act-enforcement.pdf",
    },
]


def main():

    print()
    print("=" * 80)
    print("METADATA EXTRACTION TEST")
    print("=" * 80)

    for case in TEST_CASES:

        metadata = extract_metadata(
            title=case["title"],
            url=case["url"],
        )

        print()
        print("-" * 80)

        print(
            f"TITLE       : "
            f"{case['title']}"
        )

        print(
            f"TYPE        : "
            f"{metadata['document_type']}"
        )

        print(
            f"CATEGORY    : "
            f"{metadata['category']}"
        )

        print(
            f"SUBCATEGORY : "
            f"{metadata['subcategory']}"
        )

        print(
            f"STANDARD    : "
            f"{metadata['standard_number']}"
        )

        print(
            f"REVISION    : "
            f"{metadata['revision']}"
        )

        print(
            f"EDITION     : "
            f"{metadata['edition']}"
        )


if __name__ == "__main__":
    main()
from app.agents.query_understanding import (
    QueryUnderstandingAgent,
)


def main():

    agent = QueryUnderstandingAgent()

    queries = [
        "What is the latest BIS standard for cement testing?",
        "Show me the amendment for IS 456",
        "I need BIS laboratory calibration guidelines",
        "Give me the form for product certification",
    ]

    for query in queries:

        print("\n" + "=" * 70)
        print("QUERY:", query)

        result = agent.understand(query)

        print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
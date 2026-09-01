class VectorStore:
    """Placeholder vector database wrapper."""

    def __init__(self, uri):
        self.uri = uri

    def search(self, query, top_k=5):
        return [
            {"id": "doc-1", "content": f"Result for query: {query}", "score": 0.99},
            {"id": "doc-2", "content": f"Additional result for query: {query}", "score": 0.87},
        ][:top_k]

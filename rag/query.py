import chromadb

DB_PATH = "/Users/hasanrahman/dcg/rag/chroma_db"
COLLECTION = "bookly_knowledge_base"

_client = chromadb.PersistentClient(path=DB_PATH)
_collection = _client.get_collection(COLLECTION)


def search_policy_kb(query: str, n_results: int = 2) -> list[str]:
    """Search Bookly's policy/FAQ knowledge base and return the most relevant chunks."""
    results = _collection.query(query_texts=[query], n_results=n_results)
    return results["documents"][0]


if __name__ == "__main__":
    for q in [
        "How long does standard shipping take?",
        "Can I return a book from the clearance section?",
        "How do I reset my password?",
    ]:
        print(f"\nQ: {q}")
        for chunk in search_policy_kb(q):
            print(f"---\n{chunk[:200]}...")

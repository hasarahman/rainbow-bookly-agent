import re
import chromadb

KB_PATH = "/Users/hasanrahman/dcg/data/bookly_knowledge_base.md"
DB_PATH = "/Users/hasanrahman/dcg/rag/chroma_db"
COLLECTION = "bookly_knowledge_base"


def chunk_markdown(text: str) -> list[dict]:
    sections = re.split(r"(?m)^## ", text)
    chunks = []
    for section in sections[1:]:
        title, _, body = section.partition("\n")
        chunks.append({"title": title.strip(), "text": f"## {title.strip()}\n{body.strip()}"})
    return chunks


def main():
    with open(KB_PATH) as f:
        text = f.read()

    chunks = chunk_markdown(text)

    client = chromadb.PersistentClient(path=DB_PATH)
    client.delete_collection(COLLECTION) if COLLECTION in [c.name for c in client.list_collections()] else None
    collection = client.create_collection(COLLECTION)

    collection.add(
        ids=[str(i) for i in range(len(chunks))],
        documents=[c["text"] for c in chunks],
        metadatas=[{"title": c["title"]} for c in chunks],
    )

    print(f"Indexed {len(chunks)} chunks into '{COLLECTION}' at {DB_PATH}")
    for c in chunks:
        print(f"  - {c['title']}")


if __name__ == "__main__":
    main()

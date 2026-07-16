"""
Retrieval layer.
Chunks incident artifacts (alerts, deploy records, on-call notes, Slack
threads) and stores them in a local ChromaDB collection using a local
sentence-transformers embedding model (no external embedding API needed).
"""
import chromadb
from chromadb.utils import embedding_functions

_client = chromadb.PersistentClient(path="./chroma_store")

_embedder = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)

_collection = _client.get_or_create_collection(
    name="incident_artifacts", embedding_function=_embedder
)


def _chunk(text: str, max_chars: int = 800):
    """Naive fixed-size chunker. Good enough for log lines / notes / chat."""
    chunks = []
    for i in range(0, len(text), max_chars):
        chunk = text[i : i + max_chars].strip()
        if chunk:
            chunks.append(chunk)
    return chunks or [text]


def index_artifact(incident_id: str, artifact_id: str, artifact_type: str, content: str):
    chunks = _chunk(content)
    ids = [f"{artifact_id}-{i}" for i in range(len(chunks))]
    metadatas = [
        {"incident_id": incident_id, "artifact_id": artifact_id, "type": artifact_type}
        for _ in chunks
    ]
    _collection.add(documents=chunks, ids=ids, metadatas=metadatas)


def retrieve(incident_id: str, query: str, k: int = 8):
    """Semantic retrieval scoped to a single incident (multi-tenant safe)."""
    results = _collection.query(
        query_texts=[query],
        n_results=k,
        where={"incident_id": incident_id},
    )
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    return list(zip(docs, metas))

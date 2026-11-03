"""Lab only: build a synthetic chromadb 1.x palace (two collections, l2 like the live drawers) in <dir>.
Run with the zoe-data py3.12 venv interpreter. Synthetic vectors, no household data."""
import math
import os
import sys

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
import chromadb  # noqa: E402

path = sys.argv[1]
client = chromadb.PersistentClient(path=path)
for name, n, space in (("mempalace_drawers", 60, "l2"), ("mempalace_audit", 25, "cosine")):
    # small batch/sync thresholds so the HNSW index is PERSISTED to its segment files and the log purged, like the
    # live palace after 1,000 ops (otherwise chromadb would rebuild it from the log and the N-cell proves nothing)
    col = client.get_or_create_collection(
        name, metadata={"hnsw:space": space, "hnsw:batch_size": 10, "hnsw:sync_threshold": 20},
        embedding_function=None)
    ids = [f"{name}-{i}" for i in range(n)]
    embs = [[math.sin((i + 1) * (k + 1) * 0.37) for k in range(16)] for i in range(n)]   # never the zero vector
    col.add(ids=ids, embeddings=embs, documents=[f"synthetic record {i}" for i in range(n)])
print("palace built", path)

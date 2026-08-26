"""General (non-sensitive) long-term memory: Chroma, local + embedded, persisted to disk.

Never stores email/calendar/file content — that's the separate sensitive store
(local Qwen3 only), not built yet. This one holds weather/gold/news/general history.
"""

import time
import uuid

import chromadb

from sunday import config

_client = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is None:
        persist_dir = config.REPO_ROOT / "data" / "memory" / "general_chroma"
        persist_dir.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(persist_dir))
        _collection = _client.get_or_create_collection("general_memory")
    return _collection


def add_interaction(task: str, sub_agent_result: str, final_response: str) -> None:
    collection = _get_collection()
    doc = f"User asked: {task}\nData gathered: {sub_agent_result}\nSunday replied: {final_response}"
    collection.add(documents=[doc], ids=[str(uuid.uuid4())], metadatas=[{"ts": time.time()}])


def retrieve_context(query: str, k: int = 3) -> str:
    collection = _get_collection()
    count = collection.count()
    if count == 0:
        return ""
    result = collection.query(query_texts=[query], n_results=min(k, count))
    docs = result.get("documents", [[]])[0]
    return "\n---\n".join(docs) if docs else ""

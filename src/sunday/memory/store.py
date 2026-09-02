"""Long-term memory: Chroma, on disk, written through on every finished turn.

Writing every turn to disk straight away is what makes a crash harmless. The
session summary is a bonus; the per-turn records are the real backup.

Ranking has no idea of "nothing". Vector search does not answer "is anything
relevant?" -- it answers "which are closest?", and something is always closest
even when everything is far away. So retrieval takes the top k *and then*
throws away anything past a distance cutoff. Returning zero documents is a
normal outcome.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sunday import config
from sunday.state import Provenance, Result

COLLECTION = "turns"

#: Cosine, so the configured cutoff means what it looks like it means: 0 is
#: identical, 1 is unrelated. Chroma's default is squared L2, on which 0.45
#: would be a different and much stricter thing.
SPACE = "cosine"

_RANK: dict[str, int] = {"user": 0, "public": 1, "private": 2, "secret": 3}


def strongest(results: list[Result]) -> Provenance:
    """A turn is as private as the most private thing in it."""
    label: Provenance = "user"
    for result in results:
        if _RANK[result.provenance] > _RANK[label]:
            label = result.provenance
    return label


@dataclass
class Recalled:
    text: str
    distance: float
    ts: float
    session_id: str
    provenance: str
    kind: str

    def render(self) -> str:
        when = time.strftime("%Y-%m-%d", time.localtime(self.ts))
        return f"[{when}] {self.text}"


class LongTermMemory:
    """One collection. Private and public turns live in it together, labelled
    rather than separated: the boundary that matters is the airlock, not the
    filing cabinet."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or config.MEMORY_DIR
        self._collection: Any = None

    # -- lazily opened, so importing this module never touches the disk --

    @property
    def collection(self) -> Any:
        if self._collection is None:
            import chromadb

            self._path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(self._path))
            self._collection = client.get_or_create_collection(
                COLLECTION, metadata={"hnsw:space": SPACE}
            )
        return self._collection

    def count(self) -> int:
        try:
            return self.collection.count()
        except Exception:  # noqa: BLE001 - memory must never break a turn
            return 0

    # -- writing --------------------------------------------------------

    def add_turn(
        self,
        *,
        task: str,
        response: str,
        session_id: str,
        results: list[Result],
        provenance: Provenance | None = None,
    ) -> str | None:
        document = f"You: {task}\nSunday: {response}"
        metadata = {
            "ts": time.time(),
            "session_id": session_id,
            "tools_used": ",".join(r.tool for r in results),
            "provenance": provenance or strongest(results),
            "kind": "turn",
        }
        return self._add(document, metadata)

    def add_session_summary(self, *, summary: str, session_id: str, provenance: str) -> str | None:
        metadata = {
            "ts": time.time(),
            "session_id": session_id,
            "tools_used": "",
            "provenance": provenance,
            "kind": "session",
        }
        return self._add(summary, metadata)

    def _add(self, document: str, metadata: dict[str, Any]) -> str | None:
        if not document.strip():
            return None
        doc_id = uuid.uuid4().hex
        try:
            self.collection.add(documents=[document], ids=[doc_id], metadatas=[metadata])
        except Exception:  # noqa: BLE001
            return None
        return doc_id

    # -- reading --------------------------------------------------------

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        cutoff: float | None = None,
        exclude_session: str | None = None,
    ) -> list[Recalled]:
        cfg = config.get().memory
        top_k = top_k if top_k is not None else cfg.top_k
        cutoff = cutoff if cutoff is not None else cfg.distance_cutoff

        total = self.count()
        if total == 0 or not query.strip():
            return []

        try:
            raw = self.collection.query(
                query_texts=[query],
                n_results=min(top_k, total),
                include=["documents", "metadatas", "distances"],
            )
        except Exception:  # noqa: BLE001
            return []

        documents = (raw.get("documents") or [[]])[0]
        metadatas = (raw.get("metadatas") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]

        out: list[Recalled] = []
        for text, meta, distance in zip(documents, metadatas, distances):
            meta = meta or {}
            if exclude_session and meta.get("session_id") == exclude_session:
                continue
            if distance is not None and distance > cutoff:
                continue
            out.append(
                Recalled(
                    text=text,
                    distance=float(distance if distance is not None else 0.0),
                    ts=float(meta.get("ts", 0.0)),
                    session_id=str(meta.get("session_id", "")),
                    provenance=str(meta.get("provenance", "private")),
                    kind=str(meta.get("kind", "turn")),
                )
            )
        return out

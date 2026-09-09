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
import warnings
from dataclasses import dataclass, replace
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
    tools_used: str = ""

    def render(self) -> str:
        """Recall what you were told and what you did -- not what you said.

        A recalled turn hands the model its own past prose, and a 2b treats
        that as the template for the new answer: ask the silver price twice
        and the second reply repeats the first one's invented trend, with a
        fresh number pasted in. So a turn renders as the user's line plus the
        tools that ran. Session summaries render whole, because the fold
        prompt already wrote them as third-person notes rather than as a reply
        to copy.
        """
        when = time.strftime("%Y-%m-%d", time.localtime(self.ts))
        if self.kind != "turn":
            return f"[{when}] {self.text}"

        # Split on the first newline rather than on a name. The stored
        # document is exactly "You: <task>\n<name>: <response>", and the name
        # is configurable -- so matching it would stop working the day it
        # changed, silently, on every document written before the change.
        asked = self.text.split("\n", 1)[0].strip()
        line = f"[{when}] {asked}"
        if self.tools_used:
            line += f"\n(answered using: {self.tools_used})"
        return line


@dataclass
class Probe:
    """What one retrieval actually did, for the backstage trace.

    Retrieval swallows every exception on purpose -- memory must never be the
    reason a turn fails. The cost of that is three very different outcomes
    looking identical from outside: an empty store, a store that was searched
    and had nothing close enough, and a store that could not be opened at all.
    Only the last one is a fault, and without this record nobody could tell.
    """

    filed: int = 0        #: documents in the collection
    pulled: int = 0       #: what the vector search handed back, before the cutoff
    kept: int = 0         #: what survived the cutoff
    nearest: float | None = None
    cutoff: float = 0.0
    error: str | None = None


class LongTermMemory:
    """One collection. Private and public turns live in it together, labelled
    rather than separated: the boundary that matters is the airlock, not the
    filing cabinet."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or config.MEMORY_DIR
        self._collection: Any = None
        #: The last retrieval, for the trace. Never read by the graph.
        self.last_probe = Probe()

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
            self._check_space(self._collection)
        return self._collection

    @staticmethod
    def _check_space(collection: Any) -> None:
        """`get_or_create_collection` ignores a conflicting space on an existing
        collection rather than raising, so a store created before the cosine
        change would run squared L2 under a cosine cutoff and quietly return
        nothing. Say so instead of failing silently."""
        try:
            space = (collection.metadata or {}).get("hnsw:space")
        except Exception:  # noqa: BLE001 - never break a turn over a warning
            return
        if space and space != SPACE:
            warnings.warn(
                f"Chroma collection {COLLECTION!r} uses {space!r}, not {SPACE!r}. "
                f"The distance_cutoff in config assumes cosine, so retrieval "
                f"will behave unexpectedly. Delete the store or migrate it.",
                RuntimeWarning,
                stacklevel=2,
            )

    def count(self) -> int:
        try:
            return self.collection.count()
        except Exception as exc:  # noqa: BLE001 - memory must never break a turn
            self.last_probe = replace(
                self.last_probe, error=f"{type(exc).__name__}: {exc}"
            )
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
        from sunday import config

        document = f"You: {task}\n{config.get().assistant.name}: {response}"
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

        self.last_probe = Probe(cutoff=cutoff)
        total = self.count()
        # count() writes an error into the probe if the store could not be
        # opened; keep it rather than stamping a clean Probe over the top.
        self.last_probe = replace(self.last_probe, filed=total)
        if total == 0 or not query.strip():
            return []

        try:
            raw = self.collection.query(
                query_texts=[query],
                n_results=min(top_k, total),
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:  # noqa: BLE001
            self.last_probe = replace(
                self.last_probe, error=f"{type(exc).__name__}: {exc}"
            )
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
                    tools_used=str(meta.get("tools_used", "")),
                )
            )

        numeric = [float(d) for d in distances if d is not None]
        self.last_probe = replace(
            self.last_probe,
            pulled=len(documents),
            kept=len(out),
            nearest=min(numeric) if numeric else None,
        )
        return out

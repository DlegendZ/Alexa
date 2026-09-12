"""Long-term memory: Chroma, on disk, written through on every finished turn.

Writing every turn to disk straight away is what makes a crash harmless. The
session summary is a bonus; the per-turn records are the real backup.

Ranking has no idea of "nothing". Vector search does not answer "is anything
relevant?" -- it answers "which are closest?", and something is always closest
even when everything is far away. So retrieval takes the top k *and then*
throws away anything past a distance cutoff. Returning zero documents is a
normal outcome.

It also has no idea of "two things". One question about two subjects embeds to
a single vector sitting between them, close to neither, and the cutoff then
throws away the half that had an answer -- which is what "it forgot everything"
looks like from outside. So a compound line is searched clause by clause as
well as whole, and the results merged. `sunday.clauses` owns the connectives,
because `fastpaths` needs the same fact for the opposite purpose.
"""

from __future__ import annotations

import time
import uuid
import warnings
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from sunday import clauses, config
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
        self._client: Any = None
        self._collection: Any = None
        #: The last retrieval, for the trace. Never read by the graph.
        self.last_probe = Probe()

    # -- lazily opened, so importing this module never touches the disk --

    @property
    def collection(self) -> Any:
        if self._collection is None:
            import chromadb

            self._path.mkdir(parents=True, exist_ok=True)
            if self._client is None:
                self._client = chromadb.PersistentClient(path=str(self._path))
            self._collection = self._client.get_or_create_collection(
                COLLECTION, metadata={"hnsw:space": SPACE}
            )
            self._check_space(self._collection)
        return self._collection

    def forget_all(self) -> int:
        """Empty the store, and say how much was in it.

        The collection is dropped and made again rather than the documents
        being deleted one by one: an emptied HNSW index is not the same thing
        as a new one, and the point of this button is a store that behaves like
        a first run.

        The client is kept open across the drop. Chroma is SQLite underneath,
        and a second process -- or a second client in this one -- opening the
        same directory mid-write is how a store gets corrupted; there is
        already a note in this repository about a whole session spent on
        exactly that.

        Failure is raised rather than swallowed. Everywhere else in this class
        an exception is caught, because memory must never be the reason a turn
        fails -- but this is not a turn. It is a button somebody pressed, and
        "it did not work" is the only honest answer to give them.
        """
        removed = self.collection.count()
        self._client.delete_collection(COLLECTION)
        self._collection = None
        # Open the new one now, while there is somebody to tell if it will not
        # open. Left until the next turn, a failure here would surface as
        # retrieval quietly returning nothing.
        self._collection = self._client.get_or_create_collection(
            COLLECTION, metadata={"hnsw:space": SPACE}
        )
        self.last_probe = Probe()
        return removed

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

    def _search(self, query: str, n_results: int) -> list[tuple[str, dict, float]]:
        """One vector search. Raises; the caller decides what a failure means."""
        raw = self.collection.query(
            query_texts=[query],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
        documents = (raw.get("documents") or [[]])[0]
        metadatas = (raw.get("metadatas") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]
        return [
            (text, meta or {}, float(distance if distance is not None else 0.0))
            for text, meta, distance in zip(documents, metadatas, distances)
        ]

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        cutoff: float | None = None,
        exclude_session: str | None = None,
    ) -> list[Recalled]:
        """Search for the line, and for each clause of it.

        One question about two subjects embeds to one vector sitting between
        them, close to neither: measured here, "what is my name" finds the
        stored name at 0.498 and "what is my name and what did we do last
        session" pushes the same document out to 0.679 -- past the cutoff, so
        the turn is handed nothing and says, correctly, that it has no record.
        That is the shape of forgetting people actually report.

        `fastpaths` already refuses a compound message for the neighbouring
        reason. This is the same fact used the other way round, which is why
        the connectives live in `sunday.clauses` rather than in either caller.

        A document found by more than one clause is kept once, at its best
        distance. Concatenating would spend the retrieved slice saying the
        same thing three times.
        """
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

        #: Best distance per document, across every clause that found it.
        best: dict[str, tuple[dict, float]] = {}
        for one in clauses.queries_for(query):
            if not one.strip():
                continue
            try:
                found = self._search(one, min(top_k, total))
            except Exception as exc:  # noqa: BLE001
                # A failure on any clause is a failure of the search, not a
                # thinner result set: reporting three of four clauses as
                # though nothing were wrong is the silence this Probe exists
                # to break.
                self.last_probe = replace(
                    self.last_probe, error=f"{type(exc).__name__}: {exc}"
                )
                return []
            for text, meta, distance in found:
                if text not in best or distance < best[text][1]:
                    best[text] = (meta, distance)

        out: list[Recalled] = []
        for text, (meta, distance) in sorted(best.items(), key=lambda kv: kv[1][1]):
            if exclude_session and meta.get("session_id") == exclude_session:
                continue
            if distance > cutoff:
                continue
            out.append(
                Recalled(
                    text=text,
                    distance=distance,
                    ts=float(meta.get("ts", 0.0)),
                    session_id=str(meta.get("session_id", "")),
                    provenance=str(meta.get("provenance", "private")),
                    kind=str(meta.get("kind", "turn")),
                    tools_used=str(meta.get("tools_used", "")),
                )
            )
            # The slice pays for what comes back, so the merge is capped at
            # the same top_k a single search was capped at. Clauses widen what
            # is *considered*, never what is handed over.
            if len(out) == top_k:
                break

        distances = [distance for _, distance in best.values()]
        self.last_probe = replace(
            self.last_probe,
            pulled=len(best),
            kept=len(out),
            nearest=min(distances) if distances else None,
        )
        return out

"""Everything the graph makes decisions on.

No `route`, no `confidence`, no handoff counter: tool selection is the routing
decision and there is only one agent to count. No growing `messages` list
either -- history is the session store's job and arrives already compressed as
`context`. The token stream is not in state, because state is what the graph
decides on and the stream is delivery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

Provenance = Literal["user", "public", "private", "secret"]

#: Which provenance labels may be seen by the airlock when it composes an
#: outgoing query. Everything else is structurally out of scope there.
AIRLOCK_VISIBLE: frozenset[str] = frozenset({"user", "public"})


@dataclass
class Result:
    """One tool result, carrying the label that decides where it may travel."""

    tool: str
    args: dict[str, Any]
    content: str
    provenance: Provenance
    ok: bool = True
    truncated: bool = False

    def label(self) -> str:
        return f"[{self.tool} -> {self.provenance}]"


class SundayState(TypedDict, total=False):
    # --- turn ---
    task: str
    modality: Literal["text", "voice"]
    session_id: str
    trace_id: str

    # --- memory (read before anything else) ---
    context: str  # summary + recent + retrieved
    context_tokens: int  # against the Stage 02 slices
    #: The user's own last few questions, for the airlock and nothing else.
    #: `context` never reaches it -- that carries replies and retrieved turns,
    #: which may quote a private file. These are the user's words, which is
    #: what `task` is, so they carry the same label and the same permission.
    asked_before: list[str]

    # --- provenance & the door ---
    tainted: bool  # a credential path was read -> door shut
    blocked: bool  # a call to ask_external was refused
    redactions: int  # key shapes stripped this turn
    # Audit only. The airlock filters by Result.provenance through
    # AIRLOCK_VISIBLE, which is the stronger mechanism -- this records, for the
    # log line, whether the turn touched anything private at all.
    saw_private: bool

    # --- bounded loops ---
    tool_calls: int  # hard cap from config [limits]
    hops: int  # web hops, hard cap from config [external]
    thinking: bool  # set in code, never by the model

    # --- output ---
    tool_results: list[Result]  # each carries provenance
    final_response: str  # assembled from the stream
    unresolved: str | None  # set when a cap or the door stops us
    committed: bool  # false if cancelled -> skip memory_write


def new_state(
    task: str,
    *,
    session_id: str,
    trace_id: str,
    modality: Literal["text", "voice"] = "text",
) -> SundayState:
    return SundayState(
        task=task,
        modality=modality,
        session_id=session_id,
        trace_id=trace_id,
        context="",
        context_tokens=0,
        asked_before=[],
        tainted=False,
        blocked=False,
        redactions=0,
        saw_private=False,
        tool_calls=0,
        hops=0,
        thinking=False,
        tool_results=[],
        final_response="",
        unresolved=None,
        committed=False,
    )


@dataclass
class TurnEvents:
    """Side-channel for things the UI wants to see but the graph does not
    decide on: tool activity, notices, the token stream."""

    notices: list[str] = field(default_factory=list)

    def notice(self, text: str) -> None:
        if text not in self.notices:
            self.notices.append(text)

"""The pieces of the tool loop that are worth testing on their own.

The loop itself lives in `sunday.runtime`, because it needs the graph's state
and the airlock. What is here is decision code with no I/O in it.
"""

from __future__ import annotations

from typing import Any

from sunday.agent import prompts
from sunday.state import Result, SundayState

#: Tool-selection rounds only exist to see whether a tool call appears; their
#: prose is thrown away, so there is no reason to generate a whole reply.
TOOL_ROUND_MAX_TOKENS = 384


def build_messages(state: SundayState) -> list[dict[str, Any]]:
    """The opening message list for a turn: system prompt, whatever memory
    returned, and what the user actually said."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": prompts.SYSTEM}]
    context = state.get("context") or ""
    if context.strip():
        messages.append(
            {
                "role": "system",
                "content": (
                    "A record of earlier conversations, for reference only. "
                    "'You:' lines are the user speaking; facts they stated "
                    "about themselves still hold, and when you use one, speak "
                    "about them in the second person -- their landlord, not "
                    "yours. Older entries show what "
                    "they asked before, not what you answered -- if this turn "
                    "needs those figures, get them again with a tool. Answer "
                    "the question you were just asked, in your own words.\n\n"
                    + context
                ),
            }
        )
    messages.append({"role": "user", "content": state["task"]})
    return messages


def tool_message(result: Result) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_name": result.tool,
        "content": result.content,
    }


def should_think(state: SundayState) -> bool:
    """Thinking is a dial, and code holds it. The model never opts in.

    Any of: three or more tool results to reconcile, a private half and a
    public half to weave together, the guardrail shut the door, or a cap was
    reached and the gap has to be explained.
    """
    results = state.get("tool_results") or []
    if len(results) >= 3:
        return True
    if state.get("blocked") or state.get("unresolved"):
        return True

    kinds = {r.provenance for r in results}
    private_half = bool(kinds & {"private", "secret"})
    public_half = "public" in kinds
    return private_half and public_half


def compose_instruction(state: SundayState) -> dict[str, Any] | None:
    """The last nudge before the final pass, when something went wrong."""
    unresolved = state.get("unresolved")
    if not unresolved:
        return None
    return {
        "role": "system",
        "content": prompts.UNRESOLVED_HINT.format(unresolved=unresolved),
    }


def list_instruction(state: SundayState) -> dict[str, Any] | None:
    """The other last nudge: a multi-line tool result reaches the model as the
    nearest thing it read, and it answers in that shape.

    Separate from `compose_instruction` because the two can both apply -- a
    folder listing that also hit a cap needs the gap explained *and* the list
    spoken -- and because this one is about form where that one is about
    honesty.
    """
    for result in state.get("tool_results") or []:
        if result.ok and "\n" in result.content.strip():
            return {"role": "system", "content": prompts.LIST_HINT}
    return None


def summarise_results(results: list[Result]) -> str:
    """One line per tool result, for logs and for the unresolved message."""
    return "; ".join(f"{r.tool}={'ok' if r.ok else 'error'}" for r in results)

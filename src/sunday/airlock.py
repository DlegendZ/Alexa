"""The airlock.

When the agent wants the web it states an intent, not a query. The intent is a
hint. The query is written by a separate model call with a fresh context
containing only: what the user said this turn, any `public` results already
gathered, and the hint.

No conversation history. No retrieved memory. No `private` or `secret` results.
The query writer cannot mention your sell target because your sell target was
never in the room -- a structural guarantee, not a careful model.

Milestone 4 fills in `run`; the composer below is the part that has to be right.
"""

from __future__ import annotations

from dataclasses import dataclass

from sunday.agent import prompts
from sunday.agent.llm import Agent
from sunday.state import AIRLOCK_VISIBLE, Result, SundayState


@dataclass
class Cleared:
    """What survived composition and the guardrail: one query string."""

    query: str
    redactions: int


def visible_results(results: list[Result]) -> list[Result]:
    """Only what may cross. `private` and `secret` are not filtered later --
    they are never assembled into the prompt at all."""
    return [r for r in results if r.provenance in AIRLOCK_VISIBLE and r.ok]


def compose_prompt(state: SundayState, intent: str) -> list[dict[str, str]]:
    """Build the fresh context. This function is the airlock: everything it
    does not put in is everything that cannot get out."""
    parts = [f"The user asked: {state['task']}"]
    public = visible_results(state.get("tool_results") or [])
    if public:
        parts.append(
            "Public information already gathered this turn:\n"
            + "\n".join(f"- {r.content}" for r in public)
        )
    parts.append(f"What to find out: {intent}")
    parts.append("Write the search query.")
    return [
        {"role": "system", "content": prompts.AIRLOCK_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def compose(agent: Agent, state: SundayState, intent: str) -> Cleared:
    """One model call, fresh context, one query out.

    Scrubbing happens here and only here, and the count comes back with the
    query. Scrubbing twice would be harmless for the data and fatal for the
    telling: the second pass counts zero, because `[redacted]` contains no key
    shape, and the notice that says a credential was stripped would never fire.
    """
    from sunday import guardrail

    reply = agent.chat(compose_prompt(state, intent), think=False, max_tokens=64)
    query = (reply.content or "").strip().strip('"').strip()
    if not query:
        # Fall back to the user's own words, never to the intent: the intent
        # was written by the agent, which had the private half in scope.
        query = state["task"]
    query, redactions = guardrail.scrub_query(query)
    return Cleared(query=query, redactions=redactions)

"""The airlock.

When the agent wants the web it states an intent, not a query. The intent is a
hint. The query is written by a separate model call with a fresh context
containing only: what the user said this turn, any `public` results already
gathered, and the hint.

No conversation history. No retrieved memory. No `private` or `secret` results.
The query writer cannot mention your sell target because your sell target was
never in the room -- a structural guarantee, not a careful model.

Except for the hint, which was the hole. The hint is written by the agent, and
the agent *has* seen the private half; "the query writer cannot leak" was true
while "the thing that writes the hint cannot leak" was only a hope. So the hint
is vetted before it is allowed in: every word of it has to already appear in
the material that was going to cross anyway. It can reorder and it can select,
which is all a hint was ever for. It cannot introduce.

Milestone 4 fills in `run`; the composer below is the part that has to be right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sunday.agent import prompts
from sunday.agent.llm import Agent
from sunday.state import AIRLOCK_VISIBLE, Result, SundayState


@dataclass
class Cleared:
    """What survived composition and the guardrail: one query string."""

    query: str
    redactions: int
    #: Words the agent put in its hint that were not in the cleared material.
    #: Not necessarily a leak attempt -- a 2b paraphrases -- but it is the only
    #: number that says how often the hint tried to introduce something.
    intent_words_dropped: int = 0


def visible_results(results: list[Result]) -> list[Result]:
    """Only what may cross. `private` and `secret` are not filtered later --
    they are never assembled into the prompt at all."""
    return [r for r in results if r.provenance in AIRLOCK_VISIBLE and r.ok]


#: A word, for vetting. Keeps digits and inner punctuation together, so
#: `4,600` and `sell-target` are each one token rather than several that
#: individually look harmless.
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'.,_-]*")

#: Words too common to carry anything, allowed through even when the cleared
#: material does not happen to contain them. Deliberately tiny and closed: it
#: is the difference between a hint that reads as English and one that reads as
#: a keyword salad, and every entry is a word that cannot identify anything.
_FILLER = frozenset(
    """a an and are as at be by for from how in is it its of on or the this to
    what when where which who why with about current latest recent news price
    prices cost costs today now""".split()
)


def vet_intent(intent: str, cleared: str) -> tuple[str, int]:
    """Keep only the words of `intent` that already appear in `cleared`.

    The hint's whole job is to say which part of what the user asked to focus
    on. Selecting and reordering words that were already going to cross does
    that. Introducing a word that was not there is the only thing it could do
    that would leak, and it is the only thing this takes away.

    Returns the vetted hint and how many words were dropped, because a silent
    filter is how you end up believing a guarantee you no longer have.
    """
    allowed = {match.group(0).lower() for match in _WORD.finditer(cleared)}
    kept: list[str] = []
    dropped = 0
    for match in _WORD.finditer(intent):
        word = match.group(0)
        if word.lower() in allowed or word.lower() in _FILLER:
            kept.append(word)
        else:
            dropped += 1
    return " ".join(kept), dropped


def compose_prompt(state: SundayState, intent: str) -> list[dict[str, str]]:
    """Build the fresh context. This function is the airlock: everything it
    does not put in is everything that cannot get out."""
    parts = []
    earlier = state.get("asked_before") or []
    if earlier:
        # Only the user's own earlier lines, so that a question which points
        # at something -- "check the internet for that" -- has a "that".
        parts.append(
            "Earlier in this conversation the user asked:\n"
            + "\n".join(f"- {q}" for q in earlier)
        )
    parts.append(f"The user asked: {state['task']}")
    public = visible_results(state.get("tool_results") or [])
    if public:
        parts.append(
            "Public information already gathered this turn:\n"
            + "\n".join(f"- {r.content}" for r in public)
        )
    if intent:
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

    # The hint is vetted against exactly the material the prompt is about to
    # contain -- not against some wider notion of "public", which would drift.
    public = visible_results(state.get("tool_results") or [])
    cleared_material = " ".join(
        [state["task"], *(state.get("asked_before") or [])]
        + [r.content for r in public]
    )
    intent, dropped = vet_intent(intent, cleared_material)

    reply = agent.chat(compose_prompt(state, intent), think=False, max_tokens=64)
    query = (reply.content or "").strip().strip('"').strip()
    if not query:
        # Fall back to the user's own words, never to the intent: the intent
        # was written by the agent, which had the private half in scope.
        query = state["task"]
    query, redactions = guardrail.scrub_query(query)
    return Cleared(query=query, redactions=redactions, intent_words_dropped=dropped)

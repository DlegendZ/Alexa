"""Backstage narration: what Sunday is doing, while it is still doing it.

Every other channel tells you the *outcome*. The token stream is the answer,
the notices are the things you are owed an apology for, the JSONL line is the
post-mortem. None of them answer the question you actually have while you
wait, which is "is it stuck, or is it reading?" -- and, the one that started
this file, "did it look in long-term memory at all, or did that quietly fail
and nobody said?"

So: one line per step, emitted as it happens, saying what ran and what came
back. The phrasing lives here rather than in the runtime for two reasons. The
runtime should read as the machine it is, and this text is meant to be *read*
by a person who is tired -- so it is allowed to be funny, as long as the facts
stay first. A joke that costs you a number is a bug in this file.

Rules for anything added here:

- Numbers before jokes. "0 kept (nearest 0.62, cutoff 0.45)" is the point; the
  aside about the cabinet is decoration.
- Say when something was *tried and came back empty*, differently from when it
  was never tried, differently again from when it broke. Those three look
  identical from outside and only one of them is fine.
- No markdown. Same reason as the replies: this may end up somewhere it gets
  read aloud, and asterisks get spoken.
- Deterministic. No random phrasings -- two runs of the same turn should
  produce two identical traces, or diffing them is worthless.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The five acts, in order. `tools` is not a graph node -- it is the part of
#: the agent node worth its own heading, because it is where the time goes.
STEPS: tuple[str, ...] = (
    "memory_read",
    "agent",
    "tools",
    "compose_reply",
    "memory_write",
)

#: What the person sees as the step marker.
HEADINGS: dict[str, str] = {
    "wake": "0/5 wake",
    "memory_read": "1/5 memory",
    "agent": "2/5 agent",
    "tools": "3/5 tools",
    "compose_reply": "4/5 reply",
    "memory_write": "5/5 filing",
    "done": "-- turn closed",
}


@dataclass(frozen=True)
class Line:
    """One backstage line. `step` is for colouring, `text` is for reading,
    `detail` is for a client that wants the raw numbers."""

    step: str
    text: str
    detail: dict[str, Any]

    def heading(self) -> str:
        return HEADINGS.get(self.step, self.step)

    def rendered(self) -> str:
        return f"{self.heading()} · {self.text}"


def _line(step: str, text: str, **detail: Any) -> Line:
    return Line(step=step, text=text, detail=detail)


# -- 0. the turn opens --------------------------------------------------


def opening(task: str, *, modality: str, trace_id: str) -> Line:
    shown = task if len(task) <= 60 else task[:57].rstrip() + "..."
    return _line(
        "wake",
        f'heard "{shown}" ({modality}). Trace {trace_id}. Nothing has been '
        f"looked at yet.",
        trace_id=trace_id,
        modality=modality,
    )


# -- 1. memory --------------------------------------------------------------
#
# The three lines this file exists for. Short-term and long-term are read on
# every single turn, before the agent is asked anything -- but "read" and
# "returned something" are different events, and only saying the second one is
# how you end up believing long-term memory is broken when it is merely quiet.


def short_term(*, exchanges: int, tokens: int) -> Line:
    if exchanges == 0:
        text = (
            "short-term: nothing in the room yet -- this is the first turn of "
            "the session, so there is no 'that file' to resolve."
        )
    else:
        text = (
            f"short-term: {exchanges} exchange(s) still in the room, "
            f"{tokens} tokens of them kept. This is what makes 'it' and "
            f"'there' mean anything."
        )
    return _line("memory_read", text, exchanges=exchanges, tokens=tokens)


def long_term(
    *,
    filed: int,
    pulled: int,
    kept: int,
    dropped: int,
    nearest: float | None,
    cutoff: float,
    error: str | None,
) -> Line:
    """Read on every turn. Whether it *found* anything is a separate fact, and
    conflating the two is exactly the confusion this line kills."""
    near = "n/a" if nearest is None else f"{nearest:.3f}"

    if error:
        text = (
            f"long-term: UNREACHABLE -- {error}. Nothing was recalled, and "
            f"that is a fault, not an empty cabinet. Fix it before trusting "
            f"anything below."
        )
    elif filed == 0:
        text = (
            "long-term: the cabinet is empty (0 filed). Read, as always, and "
            "there was genuinely nothing in it."
        )
    elif kept == 0:
        text = (
            f"long-term: {filed} filed, {pulled} pulled, 0 close enough "
            f"(nearest {near}, cutoff {cutoff}). It was read. It just had "
            f"nothing worth saying."
        )
    else:
        tail = f", {dropped} dropped as already in the room" if dropped else ""
        text = (
            f"long-term: {filed} filed, {pulled} pulled, {kept} kept "
            f"(nearest {near}, cutoff {cutoff}){tail}."
        )
    return _line(
        "memory_read",
        text,
        filed=filed,
        pulled=pulled,
        kept=kept,
        dropped=dropped,
        nearest=nearest,
        cutoff=cutoff,
        error=error,
    )


def context_built(*, tokens: int, allowance: int) -> Line:
    room = "comfortably" if tokens <= allowance else "over budget, so clipped"
    return _line(
        "memory_read",
        f"context assembled: {tokens} tokens against an allowance of "
        f"{allowance} -- {room}. The agent now knows what you know.",
        tokens=tokens,
        allowance=allowance,
    )


# -- 2. the agent, and 3. its tools -----------------------------------------


def roots_bound(roots: list[str]) -> Line:
    listed = ", ".join(roots) if roots else "none -- no folder is readable"
    return _line(
        "agent",
        f"folders it may open this turn: {listed}. Anything else gets refused "
        f"before the disk is touched.",
        roots=roots,
    )


def tools_bound(names: list[str], *, withdrawn: list[str]) -> Line:
    text = f"tools on the belt: {', '.join(names) or 'none'}"
    if withdrawn:
        text += f" (withdrawn: {', '.join(withdrawn)})"
    return _line("agent", text + ".", bound=names, withdrawn=withdrawn)


def fast_path(tool: str, args: dict[str, Any]) -> Line:
    return _line(
        "tools",
        f"fast path: {tool}({_args(args)}) ran in code before the model was "
        f"asked anything. Some questions do not need a vote.",
        tool=tool,
        args=args,
    )


def round_start(n: int) -> Line:
    return _line("agent", f"round {n}: asking the model what it wants to do.", round=n)


def model_wants(n: int, names: list[str]) -> Line:
    return _line(
        "agent",
        f"the model wants {n} tool call(s): {', '.join(names)}.",
        count=n,
        names=names,
    )


def second_chance() -> Line:
    return _line(
        "agent",
        "it answered without calling anything. Asking once more, in case it "
        "talked itself out of a job it was actually asked to do.",
    )


def model_is_ready() -> Line:
    return _line(
        "agent",
        "no more tool calls -- it thinks it has enough and is ready to talk.",
    )


def tool_started(name: str, args: dict[str, Any], scope: str) -> Line:
    where = "leaves this machine" if scope == "external" else "on this machine"
    return _line(
        "tools",
        f"running {name}({_args(args)}) -- {where}.",
        tool=name,
        args=args,
        scope=scope,
    )


def tool_finished(
    name: str, *, ok: bool, provenance: str, tokens: int, truncated: bool
) -> Line:
    verdict = "ok" if ok else "REFUSED/ERROR"
    clip = ", clipped to fit the window" if truncated else ""
    return _line(
        "tools",
        f"{name} -> {verdict}, {tokens} tokens, labelled {provenance}{clip}.",
        tool=name,
        ok=ok,
        provenance=provenance,
        tokens=tokens,
        truncated=truncated,
    )


def cap_spent(cap: int) -> Line:
    return _line(
        "tools",
        f"tool budget spent: {cap}/{cap}. Anything still queued gets a refusal "
        f"rather than a run, and the reply has to admit the gap.",
        cap=cap,
    )


# -- the door ---------------------------------------------------------------


def tainted(path: str) -> Line:
    return _line(
        "tools",
        f"credential path touched ({path}). The web door is bolted for the "
        f"rest of this turn and ask_external is off the belt. This is the "
        f"whole point of the design, working.",
        path=path,
    )


def redacted(count: int) -> Line:
    return _line(
        "tools",
        f"{count} key-shaped thing(s) scrubbed before the model saw them.",
        count=count,
    )


def blocked(name: str, why: str) -> Line:
    return _line("tools", f"{name} refused: {why}", tool=name, why=why)


def intent_trimmed(count: int) -> Line:
    return _line(
        "tools",
        f"{count} word(s) of the agent's hint were not in the cleared material, "
        f"so they did not go into the query. The hint may reorder what was "
        f"already crossing; it may not add to it.",
        dropped=count,
    )


def query_left(query: str) -> Line:
    return _line(
        "tools",
        f'the only text that left this machine: "{query}". Composed in a room '
        f"your private things were never in.",
        query=query,
    )


# -- confirmation -----------------------------------------------------------


def confirming(action: str, path: str) -> Line:
    return _line(
        "tools",
        f"stopping to ask you before {action} {path}. Waiting on a human.",
        action=action,
        path=path,
    )


def confirmed(action: str, path: str, *, approved: bool, asked: bool = True) -> Line:
    if not asked:
        text = (
            f"{action} {path} needed a yes and there was nobody attached to "
            f"give one, so the file was left alone. Silence is not consent."
        )
    elif approved:
        text = f"you said yes, so {action} {path} went ahead."
    else:
        text = f"you said no, so {path} was left exactly as it was."
    return _line("tools", text, action=action, path=path, approved=approved)


# -- 4. the reply -----------------------------------------------------------


def thinking(on: bool, why: str) -> Line:
    if on:
        return _line(
            "compose_reply",
            f"thinking: ON ({why}). Code turned this dial, not the model.",
            thinking=True,
            why=why,
        )
    return _line(
        "compose_reply",
        "thinking: off -- nothing to reconcile, so straight to the answer.",
        thinking=False,
    )


def speaking() -> Line:
    return _line("compose_reply", "generating the reply now. Tokens on their way.")


# -- 5. filing --------------------------------------------------------------


def not_written(why: str) -> Line:
    return _line(
        "memory_write",
        f"nothing filed: {why}. This turn will not exist tomorrow.",
        why=why,
    )


def written(*, exchanges: int, provenance: str, filed: int, folded: bool) -> Line:
    text = (
        f"filed: short-term now holds {exchanges} exchange(s); long-term now "
        f"holds {filed}, this one labelled {provenance}."
    )
    if folded:
        text += (
            " The oldest half was folded into a summary, because the recent "
            "slice had outgrown its allowance."
        )
    return _line(
        "memory_write",
        text,
        exchanges=exchanges,
        provenance=provenance,
        filed=filed,
        folded=folded,
    )


# -- the curtain ------------------------------------------------------------


def closing(
    *,
    tools: int,
    hops: int,
    redactions: int,
    tainted_door: bool,
    committed: bool,
    ms: int,
) -> Line:
    door = "bolted" if tainted_door else "open"
    kept = "remembered" if committed else "discarded"
    return _line(
        "done",
        f"{tools} tool call(s), {hops} web hop(s), {redactions} redaction(s), "
        f"door {door}, {ms} ms, {kept}.",
        tools=tools,
        hops=hops,
        redactions=redactions,
        tainted=tainted_door,
        committed=committed,
        ms=ms,
    )


def _args(args: dict[str, Any]) -> str:
    """Arguments, short enough to sit on one line. `text=` on a write is the
    whole new file, and nobody wants that in the margin."""
    parts: list[str] = []
    for key, value in args.items():
        shown = str(value)
        if len(shown) > 48:
            shown = shown[:45].rstrip() + "..."
        parts.append(f"{key}={shown}")
    return ", ".join(parts)

"""Short-term memory: RAM, for as long as the session lasts.

Every turn reads both this and the long-term store -- they are not
alternatives. This one is what makes "that file" and "there" resolvable.

Which it could not do, for a while, because it recorded what was *said* and not
what was *touched*. "Move it back to documents" needs the path the last turn
moved, and the last reply was "the file has been moved to the work folder" --
no path in it anywhere. The model filled the gap with the path from two turns
ago, which was the one place the file was no longer at. So an exchange now
carries the paths its tools were called with.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from sunday import config
from sunday.memory import budget


#: Argument names that name a file or a folder. The values are what makes a
#: pronoun resolvable next turn; everything else in a call is noise here.
PATH_ARGS = ("path", "source", "destination")

#: Long enough for two Windows paths, short enough that a turn full of tool
#: calls cannot crowd out the turns around it.
TOOLS_LINE_CHARS = 200


@dataclass
class Exchange:
    task: str
    response: str
    ts: float = field(default_factory=time.time)
    #: One rendered line per tool call, paths included. Empty for a turn that
    #: called nothing.
    tools: str = ""

    def render(self, name: str | None = None) -> str:
        from sunday import config

        who = name or config.get().assistant.name
        line = f"You: {self.task}\n{who}: {self.response}"
        if self.tools:
            # Below the reply, framed as a note rather than as speech: the 2b
            # copies whatever wording is nearest, and this must not come back
            # out of its mouth as prose.
            line += f"\n(files touched: {self.tools})"
        return line


def describe(results) -> str:
    """The paths a turn actually operated on, for the next turn to point at.

    Only successful calls, and only their path arguments. A refused call
    touched nothing, and offering its path back as context is how the model
    ends up retrying a path the sandbox already rejected.
    """
    parts: list[str] = []
    for result in results or []:
        if not getattr(result, "ok", False):
            continue
        args = getattr(result, "args", {}) or {}
        paths = [str(args[key]) for key in PATH_ARGS if args.get(key)]
        if paths:
            parts.append(f"{result.tool} {' -> '.join(paths)}")
    line = "; ".join(parts)
    return line[:TOOLS_LINE_CHARS].rstrip() if len(line) > TOOLS_LINE_CHARS else line


@dataclass
class SessionMemory:
    exchanges: list[Exchange] = field(default_factory=list)
    summary: str = ""
    last_activity: float = field(default_factory=time.time)

    # -- writing --------------------------------------------------------

    def add(self, task: str, response: str, results=None) -> None:
        self.exchanges.append(Exchange(task, response, tools=describe(results)))
        self.last_activity = time.time()

    def touch(self) -> None:
        self.last_activity = time.time()

    # -- reading --------------------------------------------------------

    def recent(self, tokens: int) -> str:
        """Newest first until the allowance runs out, then rendered oldest
        first so it reads in order."""
        kept: list[str] = []
        spent = 0
        for exchange in reversed(self.exchanges):
            block = exchange.render()
            cost = budget.count(block)
            if spent + cost > tokens:
                break
            kept.append(block)
            spent += cost
        return "\n\n".join(reversed(kept))

    def recent_questions(self, limit: int) -> list[str]:
        """The user's own last few lines, oldest first, and nothing else.

        Their questions, never the replies and never the tool results. This is
        the only part of the session the airlock is allowed to see, and it is
        allowed because it is the same thing `state["task"]` already is: words
        the user typed or said. A reply may quote a file; a question may not
        have quoted anything the user did not write.
        """
        return [e.task for e in self.exchanges[-limit:] if e.task.strip()]

    def overflows(self, tokens: int) -> bool:
        return budget.count(self.render_all()) > tokens

    def render_all(self) -> str:
        return "\n\n".join(e.render() for e in self.exchanges)

    # -- folding --------------------------------------------------------

    def fold(self, summarise: Callable[[str], str], tokens: int) -> bool:
        """Oldest exchanges become part of the rolling summary. Triggered by
        the recent slice crossing its allowance, not by a turn count."""
        if not self.overflows(tokens) or len(self.exchanges) < 2:
            return False

        keep_from = max(1, len(self.exchanges) // 2)
        folding, keeping = self.exchanges[:keep_from], self.exchanges[keep_from:]
        material = "\n\n".join(e.render() for e in folding)
        if self.summary:
            material = f"Summary so far:\n{self.summary}\n\n{material}"

        try:
            self.summary = summarise(material).strip()
        except Exception:  # noqa: BLE001 - a failed fold must not lose the turn
            self.summary = budget.clip(material, tokens // 2)
        self.exchanges = keeping
        return True

    # -- session boundary -----------------------------------------------

    def idle_seconds(self) -> float:
        return time.time() - self.last_activity

    def is_idle(self, cfg: config.Config | None = None) -> bool:
        """For an assistant meant to run all day, "the session ended" cannot
        mean the process exited."""
        cfg = cfg or config.get()
        return self.idle_seconds() >= cfg.memory.idle_minutes * 60

    def is_empty(self) -> bool:
        return not self.exchanges and not self.summary

    def transcript(self) -> str:
        parts = []
        if self.summary:
            parts.append(self.summary)
        parts.extend(e.render() for e in self.exchanges)
        return "\n\n".join(parts)

    def clear(self) -> None:
        self.exchanges = []
        self.summary = ""
        self.last_activity = time.time()

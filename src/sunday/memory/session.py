"""Short-term memory: RAM, for as long as the session lasts.

Every turn reads both this and the long-term store -- they are not
alternatives. This one is what makes "that file" and "there" resolvable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from sunday import config
from sunday.memory import budget


@dataclass
class Exchange:
    task: str
    response: str
    ts: float = field(default_factory=time.time)

    def render(self) -> str:
        return f"You: {self.task}\nSunday: {self.response}"


@dataclass
class SessionMemory:
    exchanges: list[Exchange] = field(default_factory=list)
    summary: str = ""
    last_activity: float = field(default_factory=time.time)

    # -- writing --------------------------------------------------------

    def add(self, task: str, response: str) -> None:
        self.exchanges.append(Exchange(task, response))
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

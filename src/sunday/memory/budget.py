"""The 8k window, split five ways.

Memory is not the only claimant. Tool results land in the window, and so does
thinking when it is switched on. Give each a fixed allowance and overflow
becomes arithmetic you can check instead of a silent truncation.

Folding is triggered by a slice crossing its allowance, not by how many turns
have happened.
"""

from __future__ import annotations

from dataclasses import dataclass

from sunday import config

#: Characters per token. An estimate on purpose: the alternative is shipping a
#: tokeniser that downloads its vocabulary on first use, which is the wrong
#: trade for an assistant whose whole point is that it works offline. Errs
#: high, so the budget is conservative rather than optimistic.
CHARS_PER_TOKEN = 3.6


def count(text: str) -> int:
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN) + 1


def clip(text: str, tokens: int, marker: str = "\n[truncated]") -> str:
    """Cut `text` to roughly `tokens`, saying so when it cuts."""
    limit = int(tokens * CHARS_PER_TOKEN)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + marker


@dataclass(frozen=True)
class Slices:
    summary: int
    recent: int
    retrieved: int
    tools: int
    thinking: int

    @property
    def total(self) -> int:
        return self.summary + self.recent + self.retrieved + self.tools + self.thinking

    def headroom(self, context_tokens: int) -> int:
        """What is left of the window once the context is in it."""
        return max(0, self.total - context_tokens)


def slices(cfg: config.Config | None = None) -> Slices:
    cfg = cfg or config.get()
    return Slices(
        summary=cfg.memory.slice_summary,
        recent=cfg.memory.slice_recent,
        retrieved=cfg.memory.slice_retrieved,
        tools=cfg.memory.slice_tools,
        thinking=cfg.models.thinking_budget,
    )


def assemble(summary: str, recent: str, retrieved: str, sl: Slices) -> tuple[str, int]:
    """Build the `context` string within the allowances, and report what it
    actually cost."""
    parts: list[str] = []
    for header, body, allowance in (
        ("Earlier in this session:\n", summary, sl.summary),
        ("Recent turns:\n", recent, sl.recent),
        ("From older conversations:\n", retrieved, sl.retrieved),
    ):
        if not body.strip():
            continue
        # The header is part of what the slice pays for, so clip the whole
        # block rather than the body alone.
        parts.append(clip(header + body, allowance))
    context = "\n\n".join(parts)
    return context, count(context)

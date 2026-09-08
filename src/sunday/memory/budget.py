"""The window, split five ways.

The window is 16384, and it belongs to the model rather than to this file. It
was 8192 for most of the build -- chosen before anything was measured -- then
32768 once it was, because on a 2b reserved KV that never fills is nearly free.
The 4b is larger and the card is not, so it came back down: past 16384 Ollama
leaves part of the model on the CPU, which costs 28% of generation speed and is
reported by nothing except `ollama ps`.

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

    def scaled_to(self, available: int) -> Slices:
        """Shrink every slice by the same factor, keeping the ratios."""
        if self.total <= 0 or available >= self.total:
            return self
        factor = available / self.total
        return Slices(
            summary=int(self.summary * factor),
            recent=int(self.recent * factor),
            retrieved=int(self.retrieved * factor),
            tools=int(self.tools * factor),
            thinking=int(self.thinking * factor),
        )


def slices(cfg: config.Config | None = None) -> Slices:
    """The five allowances, sized against what is actually free.

    The five slices are a statement of ratios, and they are not the only
    claimants: the system prompt, the bound tool schemas, the memory framing
    block and the standing system lines cost around 2260 tokens that no slice
    pays for, and the reply needs room of its own. Sizing the slices *to* the
    window rather than to what is left of it overruns `num_ctx`, and Ollama
    answers by dropping the oldest messages without saying so -- the silent
    truncation Stage 02 exists to prevent.

    So the fixed overhead comes off the top and the slices are scaled into what
    remains, keeping the ratios the config asked for.

    The shipped slices are sized so that nothing is scaled, and that is worth
    keeping true. This function is silent when it fires: carrying the old
    2048/6144/2048/4096 into a 16384 window would have shrunk every one by 0.81
    while `config.toml` went on claiming the old numbers. The scaling stays,
    because it is what makes a smaller window degrade rather than break -- but
    the configuration that ships must not be one that needs it.
    """
    cfg = cfg or config.get()
    want = Slices(
        summary=cfg.memory.slice_summary,
        recent=cfg.memory.slice_recent,
        retrieved=cfg.memory.slice_retrieved,
        tools=cfg.memory.slice_tools,
        thinking=cfg.models.thinking_budget,
    )
    available = max(
        0,
        cfg.models.context_tokens
        - cfg.models.overhead_tokens
        - cfg.models.reply_tokens,
    )
    return want.scaled_to(available)


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

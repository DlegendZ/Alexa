"""The one model, served by Ollama.

Tool rounds are non-streaming -- there is nothing to show until the tools have
run. The final pass streams, because that is the reply you are waiting for.
Thinking never reaches either sink: it comes back on its own field and is
dropped here, before the fork.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

import ollama

from sunday import config


class OllamaDown(RuntimeError):
    """Ollama is not answering, or the model is not pulled. The message is
    written for a person, not a stack trace."""


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass
class Reply:
    content: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None  #: the provider message, to be appended back verbatim


class Agent:
    """Thin wrapper over one Ollama model. Stateless: the caller owns the
    message list, because the graph owns the context budget."""

    def __init__(self, cfg: config.Config | None = None) -> None:
        self.cfg = cfg or config.get()
        self.model = self.cfg.models.agent
        self._client = ollama.Client(host=self.cfg.models.ollama_url)

    # -- health ---------------------------------------------------------

    def preflight(self) -> None:
        """Fail early and legibly. Called once at startup, not per turn."""
        try:
            listing = self._client.list()
        except Exception as exc:  # noqa: BLE001 - any transport failure reads the same
            raise OllamaDown(
                f"Cannot reach Ollama at {self.cfg.models.ollama_url}. "
                "Start it with:  ollama serve"
            ) from exc

        names = {m.model for m in listing.models if m.model}
        if self.model not in names and f"{self.model}:latest" not in names:
            raise OllamaDown(
                f"Model {self.model!r} is not pulled. Get it with:  "
                f"ollama pull {self.model}"
            )

    # -- generation -----------------------------------------------------

    def _options(self, max_tokens: int | None = None) -> dict[str, Any]:
        options: dict[str, Any] = {"num_ctx": self.cfg.models.context_tokens}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        return options

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        think: bool = False,
        max_tokens: int | None = None,
    ) -> Reply:
        try:
            response = self._client.chat(
                model=self.model,
                messages=messages,
                tools=tools or None,
                think=think,
                options=self._options(max_tokens),
            )
        except Exception as exc:  # noqa: BLE001
            raise OllamaDown(f"Ollama call failed: {exc}") from exc

        message = response.message
        calls = [
            ToolCall(name=call.function.name, args=dict(call.function.arguments or {}))
            for call in (message.tool_calls or [])
        ]
        return Reply(
            content=message.content or "",
            thinking=getattr(message, "thinking", "") or "",
            tool_calls=calls,
            raw=message,
        )

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        think: bool = False,
    ) -> Iterator[str]:
        """Yield reply tokens as they are generated. Thinking chunks are
        swallowed here so neither sink ever sees them."""
        try:
            chunks = self._client.chat(
                model=self.model,
                messages=messages,
                think=think,
                options=self._options(),
                stream=True,
            )
            for chunk in chunks:
                piece = chunk.message.content or ""
                if piece:
                    yield piece
        except Exception as exc:  # noqa: BLE001
            raise OllamaDown(f"Ollama stream failed: {exc}") from exc

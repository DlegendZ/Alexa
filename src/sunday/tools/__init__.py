"""The tool registry.

A tool is a plain function plus the two things the rest of the system needs to
know about it: the JSON schema Ollama binds, and the provenance its results
carry. Nothing here knows about the graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from sunday.state import Provenance

Scope = Literal["local", "external"]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., str]
    provenance: Provenance
    scope: Scope = "local"
    #: Whether this tool can work at all on this machine, asked fresh each
    #: time rather than decided at import.
    #:
    #: Calendar and mail need a Google client the user may never set, and
    #: every credential here is optional by design. Binding a tool that cannot
    #: run means the model calls it, reads a refusal, and relays a paragraph
    #: about OAuth to somebody who never asked for mail -- so an unconfigured
    #: tool is not bound and `list_capabilities` does not claim it.
    #:
    #: A callable and not a flag, because the answer changes while the process
    #: is running: the settings screen writes a client id and the very next
    #: turn should have the tool.
    requires: Callable[[], bool] | None = None
    #: What to tell the user when `requires` says no, in the second person and
    #: naming what would turn it on.
    #:
    #: It lives here rather than in `list_capabilities` for the reason
    #: `files.no_such_folder` exists: a rule kept at each call site is a rule
    #: that drifts. The first draft of the capability line said "because no
    #: Google client is set" for every unusable tool, which is true of the only
    #: two that have a predicate today and would be a lie about the third.
    requires_note: str = ""

    def usable(self) -> bool:
        """Configured well enough to be worth binding. Never raises -- a
        predicate that throws must not take the turn with it."""
        if self.requires is None:
            return True
        try:
            return bool(self.requires())
        except Exception:  # noqa: BLE001 - an unanswerable question is a no
            return False

    def schema(self) -> dict[str, Any]:
        """Ollama / OpenAI function-calling shape."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def invoke(self, args: dict[str, Any]) -> str:
        """Call the tool. Every exception is caught here -- the tool boundary
        is where failure becomes an ordinary result string."""
        try:
            return self.fn(**args)
        except TypeError as exc:
            return f"error: bad arguments for {self.name} ({exc})"
        except Exception as exc:  # noqa: BLE001 - a tool never crashes the turn
            return f"error: {self.name} failed ({type(exc).__name__}: {exc})"


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    _REGISTRY[tool.name] = tool
    return tool


def get(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def all_tools() -> list[Tool]:
    return list(_REGISTRY.values())


def configured() -> list[Tool]:
    """Every tool that could run on this machine as it is set up now.

    The distinction against `available()` is per-turn versus permanent. A tool
    withdrawn because the turn went tainted is still something this assistant
    can do; a tool withdrawn because nobody has entered a Google client is
    not, until somebody does. `list_capabilities` answers "what can you do",
    so it reads this one -- and the web door being bolted for the rest of a
    turn is said separately, in its own system line and its own notice.
    """
    return [tool for tool in _REGISTRY.values() if tool.usable()]


def available(*, external_enabled: bool = True, exclude: set[str] | None = None) -> list[Tool]:
    """The tools bound for one model call. `exclude` is how the door is shut
    for the rest of a tainted turn."""
    exclude = exclude or set()
    return [
        tool
        for tool in configured()
        if tool.name not in exclude
        and (external_enabled or tool.scope != "external")
    ]


def schemas(tools: list[Tool]) -> list[dict[str, Any]]:
    return [tool.schema() for tool in tools]


def _load_builtins() -> None:
    from sunday.tools import (  # noqa: F401
        assets,
        capabilities,
        external,
        files,
        google,
        weather,
    )


_load_builtins()

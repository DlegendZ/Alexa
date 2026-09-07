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


def available(*, external_enabled: bool = True, exclude: set[str] | None = None) -> list[Tool]:
    """The tools bound for one model call. `exclude` is how the door is shut
    for the rest of a tainted turn."""
    exclude = exclude or set()
    return [
        tool
        for tool in _REGISTRY.values()
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
        weather,
    )


_load_builtins()

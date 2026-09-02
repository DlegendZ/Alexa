"""The graph.

    memory_read -> agent -> compose_reply -> memory_write

Linear, because the tool loop is inside the agent node rather than a cycle
between nodes: a wrong tool guess is an ordinary tool result, not another edge.
The nodes themselves are supplied by the runtime, which owns the model, the
tools and the per-turn scratch.
"""

from __future__ import annotations

from typing import Callable, Protocol

from langgraph.graph import END, START, StateGraph

from sunday.state import SundayState

Node = Callable[[SundayState], dict]


class Nodes(Protocol):
    def memory_read(self, state: SundayState) -> dict: ...
    def agent_node(self, state: SundayState) -> dict: ...
    def compose_reply(self, state: SundayState) -> dict: ...
    def memory_write(self, state: SundayState) -> dict: ...


def build_graph(nodes: Nodes):
    graph = StateGraph(SundayState)
    graph.add_node("memory_read", nodes.memory_read)
    graph.add_node("agent", nodes.agent_node)
    graph.add_node("compose_reply", nodes.compose_reply)
    graph.add_node("memory_write", nodes.memory_write)

    graph.add_edge(START, "memory_read")
    graph.add_edge("memory_read", "agent")
    graph.add_edge("agent", "compose_reply")
    graph.add_edge("compose_reply", "memory_write")
    graph.add_edge("memory_write", END)
    return graph.compile()

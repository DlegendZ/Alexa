"""The runtime: one model, one tool loop, one turn at a time.

Everything the graph nodes need lives here -- the model handle, the tool
registry and the per-turn scratch that state deliberately does not carry. One
turn runs at a time by construction; barge-in cancels the turn in flight rather
than starting a second one alongside it.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable

from sunday import (
    airlock,
    config,
    graph as graph_module,
    guardrail,
    telemetry,
    tools as tool_registry,
    web,
)
from sunday.agent import loop as agent_loop
from sunday.agent.llm import Agent, OllamaDown
from sunday.state import Result, SundayState, TurnEvents, new_state

TokenSink = Callable[[str], None]
EventSink = Callable[[dict[str, Any]], None]

EXTERNAL_TOOL = "ask_external"
DOOR_SHUT = (
    "refused: this turn touched a credential path, so nothing may be looked up "
    "on the web. Tell the user that plainly."
)
DOOR_UNBOUND = (
    "System: this turn read a credential file, so the web lookup tool has been "
    "withdrawn for the rest of it. If the user asked for something looked up, "
    "say plainly that you could not, and why."
)


class Cancelled(Exception):
    """The turn was interrupted. Nothing about it is committed."""


class TurnContext:
    """Per-turn scratch. Not in SundayState, because state is what the graph
    decides on and a message list is not a decision."""

    def __init__(self, log: telemetry.TurnLog) -> None:
        self.messages: list[Any] = []
        self.events = TurnEvents()
        self.log = log


@dataclass
class Flags:
    """The door and its counters, threaded through one tool loop and written
    back into state when the node returns."""

    tainted: bool = False
    blocked: bool = False
    redactions: int = 0
    saw_private: bool = False
    hops: int = 0


class Runtime:
    def __init__(
        self,
        cfg: config.Config | None = None,
        *,
        agent: Agent | None = None,
    ) -> None:
        self.cfg = cfg or config.get()
        self.agent = agent or Agent(self.cfg)
        self.session_id = uuid.uuid4().hex[:12]
        self._cancel = threading.Event()
        self._ctx: TurnContext | None = None
        self._on_token: TokenSink | None = None
        self._on_event: EventSink | None = None
        self._graph = graph_module.build_graph(self)

    # -- lifecycle ------------------------------------------------------

    def preflight(self) -> None:
        self.agent.preflight()

    def cancel(self) -> None:
        """Barge-in. The turn in flight stops and is never committed."""
        self._cancel.set()

    def _check_cancelled(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    # -- plumbing -------------------------------------------------------

    @property
    def ctx(self) -> TurnContext:
        if self._ctx is None:  # pragma: no cover - defensive
            raise RuntimeError("no turn in flight")
        return self._ctx

    def _emit(self, **event: Any) -> None:
        if self._on_event is not None:
            self._on_event(event)

    def _token(self, text: str) -> None:
        if self._on_token is not None:
            self._on_token(text)

    def _bound_tools(self, flags: Flags) -> list[tool_registry.Tool]:
        """Unbinding protects the next model invocation; the in-loop check
        protects the current one. You need both, and the second is the one
        people forget."""
        exclude = {EXTERNAL_TOOL} if flags.tainted else set()
        return tool_registry.available(
            external_enabled=self.cfg.external.enabled, exclude=exclude
        )

    # -- nodes ----------------------------------------------------------

    def memory_read(self, state: SundayState) -> dict:
        """Placeholder until milestone 5. Memory is read before anything else
        happens, including before the agent decides what to do."""
        return {}

    def agent_node(self, state: SundayState) -> dict:
        ctx = self.ctx
        ctx.messages = agent_loop.build_messages(state)

        results: list[Result] = list(state.get("tool_results") or [])
        tool_calls = state.get("tool_calls", 0)
        unresolved = state.get("unresolved")
        cap = self.cfg.limits.tool_calls
        flags = Flags()
        told_about_the_door = False

        while True:
            self._check_cancelled()
            self._emit(type="state", value="thinking")
            reply = self.agent.chat(
                ctx.messages,
                tools=tool_registry.schemas(self._bound_tools(flags)),
                max_tokens=agent_loop.TOOL_ROUND_MAX_TOKENS,
            )
            if not reply.tool_calls:
                break

            ctx.messages.append(reply.raw)
            for call in reply.tool_calls:
                self._check_cancelled()
                if tool_calls >= cap:
                    unresolved = (
                        f"stopped after {cap} tool calls, so some of this is unchecked"
                    )
                    break
                tool_calls += 1
                working: SundayState = {**state, "tool_results": results}
                result = self._dispatch(call.name, call.args, working, flags)
                results.append(result)
                ctx.messages.append(agent_loop.tool_message(result))
            if unresolved:
                break

            # Withdrawing the tool is silent unless we say so. Without this the
            # turn just loses its web half and nobody is told.
            if flags.tainted and not told_about_the_door:
                ctx.messages.append({"role": "system", "content": DOOR_UNBOUND})
                told_about_the_door = True

        if flags.tainted:
            ctx.events.notice(guardrail.NOTICE_BLOCKED)
        if flags.blocked:
            unresolved = unresolved or "a web lookup was refused this turn"

        return {
            "tool_results": results,
            "tool_calls": tool_calls,
            "unresolved": unresolved,
            "tainted": flags.tainted,
            "blocked": flags.blocked,
            "redactions": flags.redactions,
            "saw_private": flags.saw_private,
            "hops": flags.hops,
        }

    # -- the tool loop's one guarded step -------------------------------

    def _dispatch(
        self,
        name: str,
        args: dict[str, Any],
        state: SundayState,
        flags: Flags,
    ) -> Result:
        """One tool call, with the door re-checked immediately before it.

        A model can emit several calls in one response, so `read_file(".ssh/config")`
        and `ask_external(...)` can arrive together, decided before either ran.
        At bind time the turn was clean, so the door was open. That is why this
        check runs per call, in order.
        """
        if name == EXTERNAL_TOOL:
            if flags.tainted:
                flags.blocked = True
                self._emit(type="blocked", name=name)
                return Result(name, args, DOOR_SHUT, "public", ok=False)
            return self._ask_external(args, state, flags)

        result = self._run_tool(name, args, state)

        # Source taint: catches secrets that look ordinary. A password in your
        # .env is just a word and no pattern will ever match it -- its path will.
        if name in {"read_file", "list_dir", "write_file"} and guardrail.is_secret_path(
            str(args.get("path", ""))
        ):
            flags.tainted = True

        # Shape match: catches known key formats wherever they came from.
        cleaned, hits = guardrail.redact(result.content)
        if hits:
            flags.redactions += hits
            self.ctx.events.notice(guardrail.NOTICE_REDACTED_RESULT)
            result = replace(result, content=cleaned)
        if result.provenance in {"private", "secret"}:
            flags.saw_private = True
        return result

    def _run_tool(self, name: str, args: dict[str, Any], state: SundayState) -> Result:
        tool = tool_registry.get(name)
        if tool is None:
            return Result(
                tool=name,
                args=args,
                content=f"error: no tool called {name!r}",
                provenance="public",
                ok=False,
            )
        self._emit(type="tool", name=tool.name, scope=tool.scope)
        content = tool.invoke(args)
        ok = not content.startswith(("error:", "refused:"))
        return Result(
            tool=tool.name,
            args=args,
            content=content,
            provenance=tool.provenance,
            ok=ok,
        )

    def _ask_external(
        self, args: dict[str, Any], state: SundayState, flags: Flags
    ) -> Result:
        if not self.cfg.external.enabled:
            return Result(
                EXTERNAL_TOOL,
                args,
                "refused: web lookups are switched off in config",
                "public",
                ok=False,
            )
        intent = str(args.get("intent") or args.get("query") or "").strip()
        self._emit(type="tool", name=EXTERNAL_TOOL, scope="external")
        query = airlock.compose(self.agent, state, intent)
        query, hits = guardrail.scrub_query(query)
        if hits:
            flags.redactions += hits
            self.ctx.events.notice(guardrail.NOTICE_REDACTED)
        self._emit(type="query", text=query)
        content, hops, ok = self._web(query)
        flags.hops += hops
        return Result(EXTERNAL_TOOL, {"query": query}, content, "public", ok=ok)

    def _web(self, query: str) -> tuple[str, int, bool]:
        """search -> decide -> fetch -> extract -> summarise, hop-capped."""
        result = web.run(query)
        if not result.ok:
            return (f"error: {result.text}", result.hops, False)
        return (result.text, result.hops, True)

    # -- the final pass -------------------------------------------------

    def compose_reply(self, state: SundayState) -> dict:
        """The agent's final pass. One more turn of generation, this time
        producing the reply you see rather than another tool call."""
        ctx = self.ctx
        think = agent_loop.should_think(state)
        hint = agent_loop.compose_instruction(state)
        if hint:
            ctx.messages.append(hint)

        self._emit(type="state", value="speaking")
        pieces: list[str] = []
        for piece in self.agent.stream(ctx.messages, think=think):
            self._check_cancelled()
            if not pieces:
                ctx.log.mark_first_token()
            pieces.append(piece)
            self._token(piece)

        final = "".join(pieces).strip()
        return {"final_response": final, "thinking": think, "committed": True}

    def memory_write(self, state: SundayState) -> dict:
        """Placeholder until milestone 5. Runs after the last token, never
        before, so a disk write cannot delay the first word."""
        return {}

    # -- one turn -------------------------------------------------------

    def run_turn(
        self,
        task: str,
        *,
        modality: str = "text",
        on_token: TokenSink | None = None,
        on_event: EventSink | None = None,
    ) -> SundayState:
        self._cancel.clear()
        trace_id = uuid.uuid4().hex[:12]
        log = telemetry.TurnLog(trace_id, modality)
        ctx = TurnContext(log)
        self._ctx = ctx
        self._on_token = on_token
        self._on_event = on_event

        state = new_state(
            task,
            session_id=self.session_id,
            trace_id=trace_id,
            modality=modality,  # type: ignore[arg-type]
        )
        try:
            final: SundayState = self._graph.invoke(state)
        except Cancelled:
            self._finish(log, cancelled=True, committed=False)
            return {**state, "committed": False, "notices": []}  # type: ignore[typeddict-unknown-key]
        except OllamaDown as exc:
            message = str(exc)
            self._emit(type="error", text=message)
            self._finish(log, error=message, committed=False)
            return {**state, "final_response": message, "committed": False, "notices": []}  # type: ignore[typeddict-unknown-key]

        notices = ctx.events.notices
        for notice in notices:
            self._emit(type="notice", text=notice)
        log.set(
            tools=[r.tool for r in final.get("tool_results", [])],
            provenance=[r.provenance for r in final.get("tool_results", [])],
            tainted=final.get("tainted", False),
            blocked=final.get("blocked", False),
            redactions=final.get("redactions", 0),
            hops=final.get("hops", 0),
            tool_calls=final.get("tool_calls", 0),
            thinking=final.get("thinking", False),
            context_tokens=final.get("context_tokens", 0),
        )
        final["notices"] = notices  # type: ignore[typeddict-unknown-key]
        self._finish(log, committed=final.get("committed", False))
        return final

    def _finish(self, log: telemetry.TurnLog, **fields: Any) -> None:
        """Emit `done`, write the log line, then drop the turn's sinks. The
        order matters: notices and `done` are emitted while a sink still
        exists to receive them."""
        log.set(**fields)
        log.write()
        self._emit(type="done", committed=bool(fields.get("committed", False)))
        self._on_token = None
        self._on_event = None
        self._ctx = None


def notices_of(state: SundayState) -> Iterable[str]:
    return state.get("notices", [])  # type: ignore[typeddict-item]

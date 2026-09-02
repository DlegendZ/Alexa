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
    fastpaths,
    graph as graph_module,
    guardrail,
    stream,
    telemetry,
    tools as tool_registry,
    web,
)
from sunday.agent import loop as agent_loop, prompts
from sunday.agent.llm import Agent, OllamaDown
from sunday.memory import LongTermMemory, SessionMemory, budget
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
        memory: LongTermMemory | None = None,
    ) -> None:
        self.cfg = cfg or config.get()
        self.agent = agent or Agent(self.cfg)
        self.memory = memory if memory is not None else LongTermMemory()
        self.session = SessionMemory()
        self.session_id = uuid.uuid4().hex[:12]
        self._cancel = threading.Event()
        self._ctx: TurnContext | None = None
        self._on_token: TokenSink | None = None
        self._on_sentence: TokenSink | None = None
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

    def _sentence(self, text: str) -> None:
        """The speech sink. Whole sentences, because a synthesiser cannot
        speak half of one."""
        if self._on_sentence is not None:
            self._on_sentence(text)

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
        """Memory is looked up before anything else happens, including before
        the agent decides what to do. Otherwise the one component that needs to
        know what "there" or "that file" refers to is the one running without
        it."""
        sl = budget.slices(self.cfg)
        recent = self.session.recent(sl.recent)
        recalled = self.memory.retrieve(state["task"])

        # Anything already in the recent block is not worth saying twice.
        fresh = [r for r in recalled if r.text not in recent]
        retrieved = "\n\n".join(r.render() for r in fresh)

        context, tokens = budget.assemble(self.session.summary, recent, retrieved, sl)
        self.ctx.log.set(
            retrieved=len(fresh),
            retrieval_scores=[round(r.distance, 3) for r in recalled],
        )
        return {
            "context": context,
            "context_tokens": tokens,
            # Retrieved memory is always private, whatever the original turn
            # was. It never reaches the airlock, which does not read context.
            "saw_private": bool(fresh),
        }

    def agent_node(self, state: SundayState) -> dict:
        ctx = self.ctx
        ctx.messages = agent_loop.build_messages(state)

        results: list[Result] = list(state.get("tool_results") or [])
        tool_calls = state.get("tool_calls", 0)
        unresolved = state.get("unresolved")
        cap = self.cfg.limits.tool_calls
        tools_budget = budget.slices(self.cfg).tools
        flags = Flags()
        told_about_the_door = False

        # Two deterministic patterns run before the model is asked anything.
        # The answer arrives as an ordinary tool result, so the turn continues
        # normally and a mixed question loses nothing.
        shortcut = fastpaths.match(state["task"])
        if shortcut is not None and not results:
            result = self._dispatch(shortcut.tool, dict(shortcut.args), state, flags)
            tool_calls += 1
            results.append(result)
            ctx.messages.append(agent_loop.tool_message(result))
            ctx.log.set(fast_path=shortcut.tool)

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

                # Tool results are a claimant on the window like any other.
                spent = sum(budget.count(r.content) for r in results)
                room = max(0, tools_budget - spent)
                if budget.count(result.content) > room:
                    result = replace(
                        result, content=budget.clip(result.content, room), truncated=True
                    )

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
            "saw_private": flags.saw_private or bool(state.get("saw_private")),
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

        def pieces() -> Any:
            first = True
            for piece in self.agent.stream(ctx.messages, think=think):
                self._check_cancelled()
                if first:
                    ctx.log.mark_first_token()
                    first = False
                yield piece

        final = stream.fork(
            pieces(), on_token=self._token, on_sentence=self._sentence
        ).strip()
        return {"final_response": final, "thinking": think, "committed": True}

    def memory_write(self, state: SundayState) -> dict:
        """Runs after the last token, never before, so a disk write cannot
        delay the first word. A cancelled or broken turn is not saved."""
        if not state.get("committed") or not state.get("final_response"):
            return {}

        task = state["task"]
        response = state["final_response"]
        results = state.get("tool_results") or []

        self.session.add(task, response)
        self.memory.add_turn(
            task=task,
            response=response,
            session_id=self.session_id,
            results=results,
        )

        sl = budget.slices(self.cfg)
        if self.session.fold(self._summarise, sl.recent):
            self.ctx.log.set(folded=True)
        return {}

    # -- summarising ----------------------------------------------------

    def _summarise(self, material: str) -> str:
        """The same local model, so folding costs time but no money."""
        reply = self.agent.chat(
            [
                {"role": "system", "content": prompts.FOLD_SYSTEM},
                {"role": "user", "content": material},
            ],
            think=False,
            max_tokens=400,
        )
        return reply.content or ""

    # -- the session boundary -------------------------------------------

    def close_session(self) -> None:
        """Write the session summary and start a new session. Triggered by
        going idle, and by shutdown."""
        if self.session.is_empty():
            return
        transcript = self.session.transcript()
        try:
            summary = self._summarise(transcript).strip()
        except OllamaDown:
            summary = budget.clip(transcript, 400)
        self.memory.add_session_summary(
            summary=summary or budget.clip(transcript, 400),
            session_id=self.session_id,
            provenance="private",
        )
        self.session.clear()
        self.session_id = uuid.uuid4().hex[:12]

    def shutdown(self) -> None:
        self.close_session()

    # -- one turn -------------------------------------------------------

    def run_turn(
        self,
        task: str,
        *,
        modality: str = "text",
        on_token: TokenSink | None = None,
        on_sentence: TokenSink | None = None,
        on_event: EventSink | None = None,
    ) -> SundayState:
        if self.session.is_idle(self.cfg):
            self.close_session()
        self.session.touch()

        self._cancel.clear()
        trace_id = uuid.uuid4().hex[:12]
        log = telemetry.TurnLog(trace_id, modality)
        ctx = TurnContext(log)
        self._ctx = ctx
        self._on_token = on_token
        self._on_sentence = on_sentence
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
        self._on_sentence = None
        self._on_event = None
        self._ctx = None


def notices_of(state: SundayState) -> Iterable[str]:
    return state.get("notices", [])  # type: ignore[typeddict-item]

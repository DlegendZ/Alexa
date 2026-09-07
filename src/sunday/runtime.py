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
    trace,
    web,
)
from sunday.tools import files
from sunday.agent import loop as agent_loop, prompts
from sunday.agent.llm import Agent, OllamaDown
from sunday.memory import LongTermMemory, SessionMemory, budget
from sunday.memory import store as memory_store
from sunday.state import Result, SundayState, TurnEvents, new_state

TokenSink = Callable[[str], None]
EventSink = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class ConfirmRequest:
    """What the person is being asked to approve.

    Structured rather than a bare string so a UI can render the stakes itself:
    the terminal reads `question`, the sidecar forwards `path` alongside it.
    """

    question: str
    path: str
    action: str = "overwrite"


#: Asks the person and blocks until they answer. The question does NOT also go
#: out as an event: an unanswerable copy of a prompt is worse than no copy, and
#: a client that rendered both had a first card whose buttons did nothing.
ConfirmSink = Callable[[ConfirmRequest], bool]

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
DOOR_OFF = (
    "refused: web lookups are switched off in config. Tell the user that "
    "plainly, and that they can re-enable it under [external] in config.toml. "
    "Do not answer the question from memory as though you had looked it up."
)
DOOR_OFF_SYSTEM = (
    "System: web lookups are switched off in this configuration, so you have no "
    "way to reach the internet this turn. If the user asks you to look something "
    "up, say plainly that you cannot rather than answering from memory."
)
HOPS_SPENT = (
    "refused: this turn has already used its {cap} web lookups. Answer with what "
    "you have and tell the user which part you could not check."
)
CAP_SPENT = (
    "refused: the tool-call cap for this turn was reached before this call ran. "
    "Answer with what you already have and say which part is unchecked."
)
NO_ROOM = (
    "refused: this result was too large for what is left of the context window. "
    "Tell the user you could not read it in full and suggest a narrower request."
)

#: Below this, a clipped result carries no information worth the confusion.
MIN_RESULT_TOKENS = 24

WRITE_DECLINED = (
    "refused: the user was asked before overwriting that file and said no. Do "
    "not try again or write it somewhere else unless they ask you to."
)
WRITE_UNATTENDED = (
    "refused: overwriting an existing file needs the user's confirmation, and "
    "there is nobody attached to this session to ask. Tell them the file was "
    "left as it was."
)
WRITE_QUESTION = "Overwrite {path} ({size})?"

DELETE_DECLINED = (
    "refused: the user was asked before deleting that file and said no. The "
    "file is still there. Do not ask again unless they bring it up."
)
DELETE_UNATTENDED = (
    "refused: deleting a file needs the user's confirmation, and there is "
    "nobody attached to this session to ask. Tell them the file is still there."
)
DELETE_QUESTION = "Delete {path} ({size})? This cannot be undone."

#: Tools whose path arguments are checked against the deny overlay. Delete is
#: in here for the same reason read is: the *name* of the file is the signal,
#: and a turn that went looking at `.ssh/` has no business on the web
#: afterwards, whatever it did when it got there.
PATH_TOOLS = {
    "read_file",
    "list_dir",
    "write_file",
    "delete_file",
    "copy_file",
    "move_file",
}

#: Every argument name that carries a path. A tool with two of them needs both
#: checked: a copy is only as safe as its more sensitive end.
PATH_ARGS = ("path", "source", "destination")


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
        self._on_confirm: ConfirmSink | None = None
        self._tracing = config.trace_enabled(self.cfg)
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

    def _trace(self, line: trace.Line) -> None:
        """One backstage line. Gated by config, because it is a lot of text --
        but on by default, because the alternative is guessing whether memory
        was read from the fact that it returned nothing."""
        if not self._tracing:
            return
        self._emit(
            type="trace",
            step=line.step,
            heading=line.heading(),
            text=line.text,
            detail=line.detail,
        )

    def _token(self, text: str) -> None:
        if self._on_token is not None:
            self._on_token(text)

    def _sentence(self, text: str) -> None:
        """The speech sink. Whole sentences, because a synthesiser cannot
        speak half of one."""
        if self._on_sentence is not None:
            self._on_sentence(text)

    def _roots_line(self) -> str:
        """Which folders are open, in the model's own message list."""
        roots = list(self.cfg.files.roots)
        if not roots:
            return prompts.NO_ROOTS_SYSTEM
        listed = "\n".join(f"- {root}" for root in roots)
        return prompts.ROOTS_SYSTEM.format(roots=listed)

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
        self._trace(
            trace.short_term(
                exchanges=len(self.session.exchanges),
                tokens=budget.count(recent),
            )
        )

        recalled = self.memory.retrieve(state["task"])

        # Anything already in the recent block is not worth saying twice, and
        # neither is the same question asked before: a turn renders as its
        # question, so an identical one adds a line of noise and no fact.
        asked = _normalise(state["task"])
        fresh = [
            r
            for r in recalled
            if r.text not in recent
            and not (r.kind == "turn" and _normalise(r.render()) == asked)
        ]
        retrieved = "\n\n".join(r.render() for r in fresh)

        # The line this whole trace was built for. "Read and found nothing",
        # "read and the store is empty" and "could not be opened at all" are
        # three different facts that all look like silence from outside.
        probe = getattr(self.memory, "last_probe", None)
        self._trace(
            trace.long_term(
                filed=getattr(probe, "filed", 0),
                pulled=getattr(probe, "pulled", len(recalled)),
                kept=len(fresh),
                dropped=len(recalled) - len(fresh),
                nearest=getattr(probe, "nearest", None),
                cutoff=getattr(probe, "cutoff", self.cfg.memory.distance_cutoff),
                error=getattr(probe, "error", None),
            )
        )

        context, tokens = budget.assemble(self.session.summary, recent, retrieved, sl)
        self._trace(
            trace.context_built(
                tokens=tokens, allowance=sl.summary + sl.recent + sl.retrieved
            )
        )
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
        # A tool that was never bound leaves no trace to explain, exactly as
        # with taint. Config-off is a standing state rather than a per-turn
        # event, so the model is told and the user hears about it only if the
        # model actually reached for the web.
        if not self.cfg.external.enabled:
            ctx.messages.append({"role": "system", "content": DOOR_OFF_SYSTEM})

        # The model cannot guess which folders it is allowed into, and a wrong
        # guess reads to the user as "Sunday cannot see my Documents folder".
        # Say it every turn, next to the tools it applies to.
        ctx.messages.append({"role": "system", "content": self._roots_line()})
        self._trace(trace.roots_bound(list(self.cfg.files.roots)))
        bound = self._bound_tools(flags)
        self._trace(
            trace.tools_bound(
                [t.name for t in bound],
                withdrawn=[
                    t.name
                    for t in tool_registry.all_tools()
                    if t.name not in {b.name for b in bound}
                ],
            )
        )

        shortcut = fastpaths.match(state["task"])
        if shortcut is not None and not results:
            result = self._dispatch(shortcut.tool, dict(shortcut.args), state, flags)
            tool_calls += 1
            results.append(result)
            ctx.messages.append(agent_loop.tool_message(result))
            ctx.log.set(fast_path=shortcut.tool)
            self._trace(trace.fast_path(shortcut.tool, dict(shortcut.args)))

        rounds = 0
        while True:
            self._check_cancelled()
            self._emit(type="state", value="thinking")
            rounds += 1
            self._trace(trace.round_start(rounds))
            reply = self.agent.chat(
                ctx.messages,
                tools=tool_registry.schemas(self._bound_tools(flags)),
                max_tokens=agent_loop.TOOL_ROUND_MAX_TOKENS,
            )
            if not reply.tool_calls:
                self._trace(trace.model_is_ready())
                break

            self._trace(
                trace.model_wants(
                    len(reply.tool_calls), [c.name for c in reply.tool_calls]
                )
            )
            ctx.messages.append(reply.raw)
            for call in reply.tool_calls:
                self._check_cancelled()
                if tool_calls >= cap:
                    # Every declared call needs an answer. The assistant message
                    # above announces N of them, so breaking outright leaves the
                    # chat template holding tool calls nothing ever replied to.
                    unresolved = (
                        f"stopped after {cap} tool calls, so some of this is unchecked"
                    )
                    self._trace(trace.cap_spent(cap))
                    skipped = Result(call.name, call.args, CAP_SPENT, "public", ok=False)
                    results.append(skipped)
                    ctx.messages.append(agent_loop.tool_message(skipped))
                    continue

                tool_calls += 1
                working: SundayState = {**state, "tool_results": results}
                result = self._dispatch(call.name, call.args, working, flags)
                results.append(self._fit(result, results, tools_budget))
                ctx.messages.append(agent_loop.tool_message(results[-1]))
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
            if not self.cfg.external.enabled:
                ctx.events.notice(guardrail.NOTICE_EXTERNAL_OFF)

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

    def _fit(self, result: Result, so_far: list[Result], tools_budget: int) -> Result:
        """Make a result fit the tools slice, or say it does not.

        Clipping to nothing produces a bare `[truncated]` marker with ok=True --
        a successful call that returned no content, which a 2b reads as licence
        to fill the gap itself. The marker means "there is more than this", so
        there has to be a this.
        """
        spent = sum(budget.count(r.content) for r in so_far)
        room = max(0, tools_budget - spent)
        if budget.count(result.content) <= room:
            return result
        if room < MIN_RESULT_TOKENS:
            return replace(result, content=NO_ROOM, ok=False, truncated=True)
        return replace(
            result, content=budget.clip(result.content, room), truncated=True
        )

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
                self._trace(
                    trace.blocked(name, "the door is bolted for this turn")
                )
                return Result(name, args, DOOR_SHUT, "public", ok=False)
            return self._ask_external(args, state, flags)

        # Read is auto-execute; replacing something you already have is not,
        # and destroying it certainly is not. This lives here rather than in
        # the tools because a tool has no channel to ask on -- the same reason
        # the door checks live here.
        if name == "write_file":
            refusal = self._confirm_overwrite(args)
            if refusal is not None:
                return refusal
        elif name == "delete_file":
            refusal = self._confirm_delete(args)
            if refusal is not None:
                return refusal
        elif name in {"copy_file", "move_file"}:
            refusal = self._confirm_landing(name, args)
            if refusal is not None:
                return refusal

        result = self._run_tool(name, args, state)

        # Source taint: catches secrets that look ordinary. A password in your
        # .env is just a word and no pattern will ever match it -- its path will.
        #
        # Only a call that actually reached the file counts. A refused path
        # returned nothing, so naming `~/.ssh/id_rsa` at a folder Sunday cannot
        # open would otherwise let the model shut its own door for the turn.
        touched = _secret_arg(name, args) if result.ok else None
        if touched is not None:
            flags.tainted = True
            self._trace(trace.tainted(touched))
            # The label exists to keep the record honest: the JSONL line and the
            # Chroma document should say a credential was touched, not "private".
            result = replace(result, provenance="secret")

        # Shape match: catches known key formats wherever they came from.
        cleaned, hits = guardrail.redact(result.content)
        if hits:
            flags.redactions += hits
            self.ctx.events.notice(guardrail.NOTICE_REDACTED_RESULT)
            self._trace(trace.redacted(hits))
            result = replace(result, content=cleaned)
        if result.provenance in {"private", "secret"}:
            flags.saw_private = True
        self._trace(
            trace.tool_finished(
                result.tool,
                ok=result.ok,
                provenance=result.provenance,
                tokens=budget.count(result.content),
                truncated=result.truncated,
            )
        )
        return result

    def _confirm_overwrite(self, args: dict[str, Any]) -> Result | None:
        """None to go ahead, or the refusal to hand back instead.

        Creating a file passes through: nothing is lost, and asking about every
        new note makes the confirmation itself something you learn to click
        past. Replacing a file you already have is the irreversible case, and
        it is the only one worth interrupting for.
        """
        target = files.resolve(str(args.get("path", "")))
        if target is None or not target.is_file():
            return None  # outside the roots, or a create -- neither asks

        return self._ask(
            tool="write_file",
            args=args,
            target=target,
            action="overwrite",
            question=WRITE_QUESTION,
            declined=WRITE_DECLINED,
            unattended=WRITE_UNATTENDED,
            declined_notice=guardrail.NOTICE_WRITE_DECLINED,
            unattended_notice=guardrail.NOTICE_WRITE_UNATTENDED,
        )

    def _confirm_delete(self, args: dict[str, Any]) -> Result | None:
        """Deleting always asks, where overwriting only asks when there is
        something to lose.

        The asymmetry is the point. A write to a path that does not exist
        leaves you with a file you did not have before; a delete of a path that
        does exist leaves you with nothing at all, and no version of that is
        safe to auto-execute. So the only cases that skip the question are the
        ones the sandbox or the tool refuses anyway.
        """
        target = files.resolve(str(args.get("path", "")))
        if target is None or not target.is_file():
            # Outside the roots, a folder, or already gone. The tool says which,
            # and there is nothing to consent to either way.
            return None

        return self._ask(
            tool="delete_file",
            args=args,
            target=target,
            action="delete",
            question=DELETE_QUESTION,
            declined=DELETE_DECLINED,
            unattended=DELETE_UNATTENDED,
            declined_notice=guardrail.NOTICE_DELETE_DECLINED,
            unattended_notice=guardrail.NOTICE_DELETE_UNATTENDED,
        )

    def _confirm_landing(self, tool: str, args: dict[str, Any]) -> Result | None:
        """A copy or a move asks about where it lands, not where it came from.

        The source survives a copy and is not lost by a move -- it is one call
        away from coming back. What cannot be undone is the file already sitting
        at the destination, so that is the one worth interrupting for, and the
        rule is the write rule: replacing asks, creating does not.
        """
        source = files.resolve(str(args.get("source", "")))
        if source is None:
            return None  # the sandbox answers first
        target = files._landing(source, str(args.get("destination", "")))
        if target is None or not target.is_file():
            return None  # outside the roots, or nothing there to lose

        return self._ask(
            tool=tool,
            args=args,
            target=target,
            action="overwrite",
            question=WRITE_QUESTION,
            declined=WRITE_DECLINED,
            unattended=WRITE_UNATTENDED,
            declined_notice=guardrail.NOTICE_WRITE_DECLINED,
            unattended_notice=guardrail.NOTICE_WRITE_UNATTENDED,
        )

    def _ask(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        target: Any,
        action: str,
        question: str,
        declined: str,
        unattended: str,
        declined_notice: str,
        unattended_notice: str,
    ) -> Result | None:
        """One human in the loop, for whichever irreversible thing it is.

        Shared rather than duplicated because the failure modes are identical
        and only one of them is obvious: nobody attached must refuse, not
        proceed, and the refusal string has to tell the model what to say --
        both of which are easy to get right once and easy to forget twice.
        """
        try:
            size = f"{target.stat().st_size} bytes"
        except OSError:  # pragma: no cover - raced or unreadable
            size = "unknown size"
        request = ConfirmRequest(
            question=question.format(path=target, size=size),
            path=str(target),
            action=action,
        )

        if self._on_confirm is None:
            # Fail closed. A session with nobody attached cannot consent, and
            # silence is not a yes.
            self.ctx.events.notice(unattended_notice.format(path=target.name))
            self._trace(
                trace.confirmed(action, str(target), approved=False, asked=False)
            )
            return Result(tool, args, unattended, "private", ok=False)

        self._trace(trace.confirming(action, str(target)))
        if not self._on_confirm(request):
            self.ctx.events.notice(declined_notice.format(path=target.name))
            self._trace(trace.confirmed(action, str(target), approved=False))
            return Result(tool, args, declined, "private", ok=False)
        self._trace(trace.confirmed(action, str(target), approved=True))
        return None

    def _run_tool(self, name: str, args: dict[str, Any], state: SundayState) -> Result:
        tool = tool_registry.get(name)
        if tool is None:
            self._trace(trace.blocked(name, "no tool by that name"))
            return Result(
                tool=name,
                args=args,
                content=f"error: no tool called {name!r}",
                provenance="public",
                ok=False,
            )
        self._emit(type="tool", name=tool.name, scope=tool.scope)
        self._trace(trace.tool_started(tool.name, args, tool.scope))
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
            flags.blocked = True
            self._emit(type="blocked", name=EXTERNAL_TOOL)
            return Result(EXTERNAL_TOOL, args, DOOR_OFF, "public", ok=False)

        # The hop cap has to refuse, not tally. `tool_calls` bounds how many
        # times the model may ask; without this, five ask_external calls make
        # five searches and five fetches under a cap that reads as two.
        if flags.hops >= self.cfg.external.max_hops:
            flags.blocked = True
            self._emit(type="blocked", name=EXTERNAL_TOOL)
            return Result(
                EXTERNAL_TOOL,
                args,
                HOPS_SPENT.format(cap=self.cfg.external.max_hops),
                "public",
                ok=False,
            )

        intent = str(args.get("intent") or args.get("query") or "").strip()
        self._emit(type="tool", name=EXTERNAL_TOOL, scope="external")

        # compose() scrubs and reports; scrubbing again here would count zero,
        # because `[redacted]` holds no key shape, and the notice would die.
        cleared = airlock.compose(self.agent, state, intent)
        if cleared.redactions:
            flags.redactions += cleared.redactions
            self.ctx.events.notice(guardrail.NOTICE_REDACTED)

        self._emit(type="query", text=cleared.query)
        # What is left for the whole turn, not for this lookup: the cap is on
        # outbound requests, so a second lookup inherits what the first spent.
        remaining = self.cfg.external.max_hops - flags.hops
        content, hops, ok = self._web(cleared.query, remaining)
        flags.hops += hops
        return Result(
            EXTERNAL_TOOL, {"query": cleared.query}, content, "public", ok=ok
        )

    def _web(self, query: str, budget: int) -> tuple[str, int, bool]:
        """search -> decide -> fetch -> extract -> summarise, hop-capped."""
        result = web.run(query, budget)
        if not result.ok:
            return (f"error: {result.text}", result.hops, False)
        return (result.text, result.hops, True)

    # -- the final pass -------------------------------------------------

    def compose_reply(self, state: SundayState) -> dict:
        """The agent's final pass. One more turn of generation, this time
        producing the reply you see rather than another tool call."""
        ctx = self.ctx
        think = agent_loop.should_think(state)
        for hint in (
            agent_loop.compose_instruction(state),
            agent_loop.list_instruction(state),
        ):
            if hint:
                ctx.messages.append(hint)
        self._trace(trace.thinking(think, _why_thinking(state)))

        self._emit(type="state", value="speaking")
        self._trace(trace.speaking())

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
            why = (
                "the turn was cancelled or failed"
                if not state.get("committed")
                else "there was no reply to file"
            )
            self._trace(trace.not_written(why))
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
        folded = self.session.fold(self._summarise, sl.recent)
        if folded:
            self.ctx.log.set(folded=True)
        self._trace(
            trace.written(
                exchanges=len(self.session.exchanges),
                provenance=memory_store.strongest(results),
                filed=self.memory.count(),
                folded=folded,
            )
        )
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
        on_confirm: ConfirmSink | None = None,
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
        self._on_confirm = on_confirm

        self._trace(trace.opening(task, modality=modality, trace_id=trace_id))
        state = new_state(
            task,
            session_id=self.session_id,
            trace_id=trace_id,
            modality=modality,  # type: ignore[arg-type]
        )
        try:
            final: SundayState = self._graph.invoke(state)
        except (Cancelled, KeyboardInterrupt):
            # Ctrl-C is a barge-in by another name. Catching it here means the
            # turn still gets its log line, its `done` event and its teardown;
            # letting it escape leaves the sinks and the turn context set.
            self._cancel.set()
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
            saw_private=final.get("saw_private", False),
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
        record = log.write()
        self._trace(
            trace.closing(
                tools=int(record.get("tool_calls", 0) or 0),
                hops=int(record.get("hops", 0) or 0),
                redactions=int(record.get("redactions", 0) or 0),
                tainted_door=bool(record.get("tainted", False)),
                committed=bool(fields.get("committed", False)),
                ms=int(record.get("total_ms", 0) or 0),
            )
        )
        self._emit(type="done", committed=bool(fields.get("committed", False)))
        self._on_token = None
        self._on_sentence = None
        self._on_event = None
        self._on_confirm = None
        self._ctx = None


def _secret_arg(name: str, args: dict[str, Any]) -> str | None:
    """The first path argument on the deny list, or None.

    Only a call that actually reached the file counts, which is why the caller
    checks `result.ok` first: naming `~/.ssh/id_rsa` at a folder Sunday cannot
    open would otherwise let the model shut its own door for the turn.
    """
    if name not in PATH_TOOLS:
        return None
    for key in PATH_ARGS:
        value = str(args.get(key, ""))
        if value and guardrail.is_secret_path(value):
            return value
    return None


def _why_thinking(state: SundayState) -> str:
    """Which of the four conditions turned the dial. "thinking: ON" with no
    reason is a fact you cannot act on, and the conditions live in
    agent_loop.should_think, which returns a bool and keeps its reasons."""
    results = state.get("tool_results") or []
    if len(results) >= 3:
        return f"{len(results)} tool results to reconcile"
    if state.get("blocked"):
        return "a lookup was refused and the gap has to be explained"
    if state.get("unresolved"):
        return "something did not complete"
    kinds = {r.provenance for r in results}
    if kinds & {"private", "secret"} and "public" in kinds:
        return "a private half and a public half to weave together"
    return "no condition matched"


def notices_of(state: SundayState) -> Iterable[str]:
    return state.get("notices", [])  # type: ignore[typeddict-item]


def _normalise(text: str) -> str:
    """For comparing a recalled question against the one just asked."""
    stripped = "".join(c for c in text.lower() if c.isalnum() or c.isspace())
    words = stripped.split()
    # A rendered turn keeps its date and the "you:" prefix; drop both.
    while words and (words[0].isdigit() or words[0] in {"you", "sunday"}):
        words.pop(0)
    return " ".join(words)

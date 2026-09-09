"""Milestone 5: write-through, the distance cutoff, the slice budgets, and the
session boundary.

The Chroma-backed tests are marked `slow` because the default embedding model
loads on first use; the rest run in milliseconds.
"""

from __future__ import annotations

import time

import pytest

from sunday.agent.llm import Reply, ToolCall
from sunday.agent import loop
from sunday.memory import budget, session as session_mod, store
from sunday.runtime import Runtime
from sunday.state import Result


# -- the window ------------------------------------------------------------


def test_the_slices_leave_room_for_everything_else_in_the_window(cfg):
    """Stage 02's claim is that overflow is arithmetic you can check. Check it.

    The slices are not the only claimants: the system prompt, the bound tool
    schemas, the memory framing block and the standing system lines cost around
    2260 tokens no slice pays for, and the reply needs room too. Sizing the
    slices *to* the window overruns num_ctx, and Ollama drops the oldest
    messages without saying so.
    """
    sl = budget.slices(cfg)
    accounted = sl.total + cfg.models.overhead_tokens + cfg.models.reply_tokens
    assert accounted <= cfg.models.context_tokens
    # The shipped slices are sized to fit, so nothing is scaled and the
    # configured numbers are the numbers in use. A scaled default is a config
    # file that lies about itself, which is note 51 in a different place.
    assert sl.recent == cfg.memory.slice_recent
    assert sl.tools == cfg.memory.slice_tools
    assert sl.summary == cfg.memory.slice_summary
    assert sl.retrieved == cfg.memory.slice_retrieved
    # And the ratios the config asked for survive the scaling.
    assert sl.recent > sl.tools > sl.summary
    assert sl.summary == sl.retrieved


def test_the_real_overhead_fits_the_reservation(cfg):
    """The reservation is a measurement, not a guess -- so measure it.

    Everything the runtime puts in the message list that no memory slice pays
    for, including the standing system lines it appends every turn. Adding a
    tool or a system line without moving the number is how num_ctx gets
    overrun, and Ollama answers that by dropping the oldest messages silently.
    """
    from sunday import runtime as runtime_module
    from sunday import tools as tool_registry
    from sunday.agent import prompts

    cfg.files.roots = ["C:/Users/User/Documents/Sunday", "E:/Work/Sunday"]
    schemas = tool_registry.schemas(tool_registry.available())
    overhead = (
        budget.count(prompts.SYSTEM)
        + budget.count(str(schemas))
        + budget.count(loop.build_messages({"task": "x", "context": "y"})[1]["content"])
        + budget.count(
            prompts.ROOTS_SYSTEM.format(
                roots="\n".join(f"- {root}" for root in cfg.files.roots)
            )
        )
        + budget.count(prompts.RETRY_HINT)
        # Only one of these can appear in a turn, so the larger one is the cap.
        + max(
            budget.count(runtime_module.DOOR_OFF_SYSTEM),
            budget.count(runtime_module.DOOR_UNBOUND),
        )
        # Same again for the two that can be appended before the final pass.
        + max(
            budget.count(prompts.LIST_HINT),
            budget.count(prompts.UNRESOLVED_HINT),
        )
    )
    assert overhead <= cfg.models.overhead_tokens


def test_a_generous_window_needs_no_scaling(cfg):
    cfg.models.context_tokens = 32768
    sl = budget.slices(cfg)
    assert sl.summary == cfg.memory.slice_summary
    assert sl.thinking == cfg.models.thinking_budget


def test_clip_says_when_it_cuts():
    out = budget.clip("word " * 500, 10)
    assert out.endswith("[truncated]")
    assert len(out) < 200


def test_assemble_stays_inside_the_allowances(cfg):
    sl = budget.slices(cfg)
    context, tokens = budget.assemble("s" * 100_000, "r" * 100_000, "t" * 100_000, sl)
    assert budget.count(context) <= sl.summary + sl.recent + sl.retrieved + 20
    assert tokens == budget.count(context)


def test_an_empty_memory_contributes_nothing(cfg):
    context, tokens = budget.assemble("", "", "", budget.slices(cfg))
    assert context == ""
    assert tokens == 0


# -- the session store -----------------------------------------------------


def test_recent_returns_newest_within_the_allowance_in_reading_order():
    mem = session_mod.SessionMemory()
    for i in range(20):
        mem.add(f"question {i}", f"answer {i}")
    text = mem.recent(60)
    assert "question 19" in text
    assert "question 0" not in text
    assert text.index("question 18") < text.index("question 19")


def test_folding_is_triggered_by_the_slice_not_the_turn_count():
    mem = session_mod.SessionMemory()
    for i in range(4):
        mem.add(f"q{i}", "a" * 200)

    assert mem.fold(lambda text: "SUMMARY", 10_000) is False  # room to spare
    assert mem.fold(lambda text: "SUMMARY", 50) is True
    assert mem.summary == "SUMMARY"
    assert len(mem.exchanges) == 2


def test_a_failed_fold_keeps_the_material_rather_than_losing_it():
    mem = session_mod.SessionMemory()
    for i in range(4):
        mem.add(f"question {i}", "a" * 200)

    def boom(text):
        raise RuntimeError("model down")

    assert mem.fold(boom, 50) is True
    assert "question 0" in mem.summary


def test_idle_is_measured_from_the_last_turn(cfg):
    cfg.memory.idle_minutes = 10
    mem = session_mod.SessionMemory()
    mem.add("hello", "hi")
    assert mem.is_idle(cfg) is False
    mem.last_activity = time.time() - 601
    assert mem.is_idle(cfg) is True


# -- provenance ------------------------------------------------------------


def test_a_turn_is_as_private_as_the_most_private_thing_in_it():
    assert store.strongest([]) == "user"
    assert store.strongest([Result("get_weather", {}, "x", "public")]) == "public"
    assert (
        store.strongest(
            [
                Result("get_weather", {}, "x", "public"),
                Result("read_file", {}, "y", "private"),
            ]
        )
        == "private"
    )
    assert (
        store.strongest(
            [
                Result("read_file", {}, "y", "private"),
                Result("read_file", {}, "z", "secret"),
            ]
        )
        == "secret"
    )


# -- the graph's two memory nodes -----------------------------------------


class FakeMemory:
    def __init__(self, recalled=None):
        self.recalled = recalled or []
        self.turns: list[dict] = []
        self.summaries: list[dict] = []
        self.queries: list[str] = []

    def retrieve(self, query, **kwargs):
        self.queries.append(query)
        return list(self.recalled)

    def add_turn(self, **kwargs):
        self.turns.append(kwargs)
        return "id"

    def add_session_summary(self, **kwargs):
        self.summaries.append(kwargs)
        return "id"

    def count(self):
        return len(self.turns)


class ScriptedAgent:
    def __init__(self, replies=None, reply_text="answered"):
        self.replies = list(replies or [])
        self.reply_text = reply_text
        self.seen: list[list] = []

    def chat(self, messages, *, tools=None, think=False, max_tokens=None):
        self.seen.append(list(messages))
        return self.replies.pop(0) if self.replies else Reply(content="")

    def stream(self, messages, *, think=False):
        self.seen.append(list(messages))
        yield self.reply_text

    def preflight(self):
        return None


def _runtime(cfg, memory, agent=None):
    return Runtime(cfg, agent=agent or ScriptedAgent(), memory=memory)  # type: ignore[arg-type]


def test_a_finished_turn_is_written_through_immediately(cfg):
    memory = FakeMemory()
    runtime = _runtime(cfg, memory)
    runtime.run_turn("what is the gold price")

    assert len(memory.turns) == 1
    assert memory.turns[0]["task"] == "what is the gold price"
    assert memory.turns[0]["response"] == "answered"
    assert runtime.session.exchanges[0].task == "what is the gold price"


def test_a_cancelled_turn_is_never_committed(cfg):
    """Barge-in: the interruption arrives while the reply is streaming."""
    memory = FakeMemory()

    class Interrupted(ScriptedAgent):
        def stream(self, messages, *, think=False):
            yield "I was saying "
            runtime.cancel()
            yield "something when you cut in"

    runtime = _runtime(cfg, memory, Interrupted())
    heard: list[str] = []
    state = runtime.run_turn("half a question", on_token=heard.append)

    assert state["committed"] is False
    assert memory.turns == []
    assert runtime.session.exchanges == []
    # The partial reply reached the speaker but never the store.
    assert heard == ["I was saying "]


def test_retrieval_reaches_the_agent_as_context(cfg):
    """A recalled turn hands over what the user said and what was done -- not
    what Sunday said back. Its own prose is the thing a 2b copies."""
    memory = FakeMemory(
        [
            store.Recalled(
                text=(
                    "You: my notes live in Documents/Sunday\n"
                    "Sunday: noted, and silver is climbing steadily"
                ),
                distance=0.2,
                ts=time.time(),
                session_id="old",
                provenance="private",
                kind="turn",
                tools_used="read_file",
            )
        ]
    )
    agent = ScriptedAgent()
    runtime = _runtime(cfg, memory, agent)
    state = runtime.run_turn("what did I say about my notes")

    assert memory.queries == ["what did I say about my notes"]
    assert "Documents/Sunday" in state["context"]  # the user's own words
    assert "climbing steadily" not in state["context"]  # Sunday's past prose
    assert "read_file" in state["context"]  # but what it did, yes
    assert state["context_tokens"] > 0
    assert state["saw_private"] is True

    system_blocks = [
        m["content"] for m in agent.seen[0] if isinstance(m, dict) and m["role"] == "system"
    ]
    assert any("Documents/Sunday" in block for block in system_blocks)


def test_a_session_summary_is_recalled_whole(cfg):
    """Unlike a turn, the fold prompt already wrote it as third-person notes,
    so there is no reply in it to copy."""
    memory = FakeMemory(
        [
            store.Recalled(
                text="The user's landlord is Pak Yusuf and rent is due on the 5th.",
                distance=0.2,
                ts=time.time(),
                session_id="old",
                provenance="private",
                kind="session",
            )
        ]
    )
    state = _runtime(cfg, memory).run_turn("who is my landlord")
    assert "Pak Yusuf" in state["context"]


def test_asking_the_same_question_again_recalls_no_noise(cfg):
    """The old turn renders as its question, which is the question just asked:
    a line of noise carrying no fact."""
    memory = FakeMemory(
        [
            store.Recalled(
                text="You: how much is silver\nSunday: $65.16",
                distance=0.05,
                ts=time.time(),
                session_id="old",
                provenance="public",
                kind="turn",
            )
        ]
    )
    state = _runtime(cfg, memory).run_turn("how much is silver")
    assert state["context"] == ""


def test_what_is_already_in_the_recent_block_is_not_retrieved_twice(cfg):
    """The stored document is deliberately written under the *old* name here.

    The assistant's name is configurable, so documents filed before it changed
    still say whatever it was called then. De-duplication has to survive that,
    which is why a recalled turn renders from its question rather than by
    matching a name it can no longer rely on.
    """
    text = "You: hello\nSunday: hi"
    memory = FakeMemory(
        [store.Recalled(text, 0.1, time.time(), "s", "private", "turn")]
    )
    runtime = _runtime(cfg, memory)
    runtime.session.add("hello", "hi")
    state = runtime.run_turn("again")

    assert state["context"].count(f"{cfg.assistant.name}: hi") == 1
    assert "Sunday: hi" not in state["context"]


def test_going_idle_writes_a_session_summary_and_starts_a_new_session(cfg):
    cfg.memory.idle_minutes = 10
    memory = FakeMemory()
    agent = ScriptedAgent(replies=[Reply(content="the user asked about gold")])
    runtime = _runtime(cfg, memory, agent)

    runtime.session.add("older question", "older answer")
    runtime.session.last_activity = time.time() - 601
    first_session = runtime.session_id

    runtime.run_turn("a new question after a long pause")

    assert len(memory.summaries) == 1
    assert memory.summaries[0]["session_id"] == first_session
    assert memory.summaries[0]["provenance"] == "private"
    assert runtime.session_id != first_session
    # The new session starts with only the turn that reopened it.
    assert [e.task for e in runtime.session.exchanges] == [
        "a new question after a long pause"
    ]


def test_shutdown_flushes_the_same_summary(cfg):
    memory = FakeMemory()
    runtime = _runtime(cfg, memory, ScriptedAgent(replies=[Reply(content="notes")]))
    runtime.session.add("q", "a")
    runtime.shutdown()

    assert len(memory.summaries) == 1
    assert runtime.session.is_empty()


def test_an_empty_session_writes_no_summary(cfg):
    memory = FakeMemory()
    runtime = _runtime(cfg, memory)
    runtime.shutdown()
    assert memory.summaries == []


def test_a_huge_tool_result_is_clipped_to_the_tools_slice(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    cfg.files.max_read_bytes = 500_000
    cfg.memory.slice_tools = 100
    (root / "big.txt").write_text("x" * 200_000, encoding="utf-8")

    agent = ScriptedAgent(
        replies=[
            Reply(tool_calls=[ToolCall("read_file", {"path": str(root / "big.txt")})]),
            Reply(content=""),
        ]
    )
    runtime = _runtime(cfg, FakeMemory(), agent)
    state = runtime.run_turn("read the big file")

    result = state["tool_results"][0]
    assert result.truncated is True
    assert budget.count(result.content) <= 110


# -- the real store --------------------------------------------------------


@pytest.mark.slow
def test_the_cutoff_lets_zero_documents_be_a_normal_outcome(cfg, tmp_path):
    memory = store.LongTermMemory(tmp_path / "chroma")
    memory.add_turn(
        task="when should I sell my gold",
        response="you said 4600",
        session_id="s1",
        results=[Result("read_file", {}, "note", "private")],
    )

    close = memory.retrieve("when do I sell gold", cutoff=0.9)
    assert len(close) == 1
    assert close[0].provenance == "private"
    assert close[0].session_id == "s1"

    far = memory.retrieve("how do I install a dishwasher", cutoff=0.2)
    assert far == []


@pytest.mark.slow
def test_metadata_rides_along_with_every_document(cfg, tmp_path):
    memory = store.LongTermMemory(tmp_path / "chroma")
    memory.add_turn(
        task="weather in Jakarta",
        response="28C and clear",
        session_id="s2",
        results=[Result("get_weather", {"city": "Jakarta"}, "28C", "public")],
    )
    [recalled] = memory.retrieve("Jakarta weather", cutoff=1.5)
    assert recalled.provenance == "public"
    assert recalled.kind == "turn"
    assert recalled.ts > 0

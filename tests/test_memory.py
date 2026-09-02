"""Milestone 5: write-through, the distance cutoff, the slice budgets, and the
session boundary.

The Chroma-backed tests are marked `slow` because the default embedding model
loads on first use; the rest run in milliseconds.
"""

from __future__ import annotations

import time

import pytest

from sunday.agent.llm import Reply, ToolCall
from sunday.memory import budget, session as session_mod, store
from sunday.runtime import Runtime
from sunday.state import Result


# -- the window ------------------------------------------------------------


def test_slices_add_up_to_the_window(cfg):
    sl = budget.slices(cfg)
    assert sl.total == cfg.models.context_tokens


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
    memory = FakeMemory(
        [
            store.Recalled(
                text="You: where do I keep my notes\nSunday: in Documents/Sunday",
                distance=0.2,
                ts=time.time(),
                session_id="old",
                provenance="private",
                kind="turn",
            )
        ]
    )
    agent = ScriptedAgent()
    runtime = _runtime(cfg, memory, agent)
    state = runtime.run_turn("what did I say about my notes")

    assert memory.queries == ["what did I say about my notes"]
    assert "Documents/Sunday" in state["context"]
    assert state["context_tokens"] > 0
    assert state["saw_private"] is True

    system_blocks = [
        m["content"] for m in agent.seen[0] if isinstance(m, dict) and m["role"] == "system"
    ]
    assert any("Documents/Sunday" in block for block in system_blocks)


def test_what_is_already_in_the_recent_block_is_not_retrieved_twice(cfg):
    text = "You: hello\nSunday: hi"
    memory = FakeMemory(
        [store.Recalled(text, 0.1, time.time(), "s", "private", "turn")]
    )
    runtime = _runtime(cfg, memory)
    runtime.session.add("hello", "hi")
    state = runtime.run_turn("again")

    assert state["context"].count("Sunday: hi") == 1


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

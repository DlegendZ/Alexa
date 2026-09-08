"""The backstage trace: what ran, said while it is running.

The failure this exists to catch is not a crash. It is the user asking "did it
read long-term memory, or did that quietly not happen?" and there being no
answer -- because an empty store, a store with nothing close enough, and a
store that could not be opened all look identical from outside.
"""

from __future__ import annotations

from sunday import config, trace
from sunday.agent.llm import Reply, ToolCall
from sunday.memory.store import Probe
from sunday.runtime import Runtime

from tests.test_review_fixes import NoMemory, Scripted


def _traces(events, step=None):
    lines = [e for e in events if e["type"] == "trace"]
    return [e for e in lines if step is None or e["step"] == step]


# -- the three silences, told apart -----------------------------------------


def test_an_empty_store_says_so():
    line = trace.long_term(
        filed=0, pulled=0, kept=0, dropped=0, nearest=None, cutoff=0.45, error=None
    )
    assert "empty" in line.text
    assert "UNREACHABLE" not in line.text


def test_a_search_that_found_nothing_says_it_was_read():
    line = trace.long_term(
        filed=339, pulled=5, kept=0, dropped=0, nearest=0.62, cutoff=0.45, error=None
    )
    assert "339" in line.text and "0.620" in line.text and "0.45" in line.text
    assert "It was read" in line.text


def test_a_broken_store_is_not_reported_as_an_empty_one():
    """The one that matters. Retrieval swallows exceptions so a turn never
    dies of a memory fault -- which means the fault has to surface here or
    nowhere."""
    line = trace.long_term(
        filed=0,
        pulled=0,
        kept=0,
        dropped=0,
        nearest=None,
        cutoff=0.45,
        error="RuntimeError: no such collection",
    )
    assert "UNREACHABLE" in line.text
    assert "no such collection" in line.text
    assert line.detail["error"]


def test_the_probe_records_what_retrieval_actually_did(sandbox, monkeypatch):
    """A real store, so the numbers in the trace are not fiction."""
    from sunday.memory import LongTermMemory

    memory = LongTermMemory(path=sandbox / "chroma")
    memory.add_turn(
        task="what is my gold sell target",
        response="7777",
        session_id="s1",
        results=[],
    )
    hits = memory.retrieve("gold sell target")

    assert memory.last_probe.filed == 1
    assert memory.last_probe.pulled == 1
    assert memory.last_probe.kept == len(hits)
    assert memory.last_probe.error is None
    assert memory.last_probe.nearest is not None


# -- the turn narrates itself ----------------------------------------------


def test_every_step_of_a_turn_is_reported(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    (root / "gold.txt").write_text("sell at 7777", encoding="utf-8")

    agent = Scripted(
        [Reply(tool_calls=[ToolCall("read_file", {"path": str(root / "gold.txt")})])]
    )
    events: list[dict] = []
    Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "what is my sell target", on_event=events.append
    )

    steps = {e["step"] for e in _traces(events)}
    assert {"wake", "memory_read", "agent", "tools", "compose_reply", "done"} <= steps

    both_halves = " ".join(e["text"] for e in _traces(events, "memory_read"))
    assert "short-term" in both_halves
    assert "long-term" in both_halves

    ran = " ".join(e["text"] for e in _traces(events, "tools"))
    assert "read_file" in ran
    assert "private" in ran  # the label it came back carrying


def test_the_folders_it_may_open_are_named_in_the_trace(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]

    events: list[dict] = []
    Runtime(cfg, agent=Scripted(), memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "hello", on_event=events.append
    )
    assert any(str(root) in e["text"] for e in _traces(events, "agent"))


def test_the_model_is_told_which_folders_are_open(cfg, tmp_path):
    """Not only the trace -- the model itself, or it guesses paths and the
    user reads the guess as a missing drive."""
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]

    agent = Scripted()
    Runtime(cfg, agent=agent, memory=NoMemory()).run_turn("hello")  # type: ignore[arg-type]

    systems = [
        m["content"]
        for m in agent.seen[0]
        if isinstance(m, dict) and m.get("role") == "system"
    ]
    assert any(str(root) in line for line in systems)


def test_done_is_still_the_last_event(cfg):
    """The closing summary goes before `done`, not after it: clients treat
    `done` as the end of the turn and stop listening."""
    events: list[dict] = []
    Runtime(cfg, agent=Scripted(), memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "hello", on_event=events.append
    )
    assert events[-1]["type"] == "done"
    assert events[-2]["type"] == "trace" and events[-2]["step"] == "done"


def test_the_trace_can_be_switched_off(cfg, monkeypatch):
    monkeypatch.setenv("SUNDAY_TRACE", "0")
    events: list[dict] = []
    Runtime(cfg, agent=Scripted(), memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "hello", on_event=events.append
    )
    assert _traces(events) == []
    # And the rest of the protocol is untouched by the silence.
    assert events[-1]["type"] == "done"


def test_the_environment_overrides_the_config(cfg, monkeypatch):
    cfg.ui.trace = False
    monkeypatch.setenv("SUNDAY_TRACE", "1")
    assert config.trace_enabled(cfg) is True
    monkeypatch.setenv("SUNDAY_TRACE", "off")
    assert config.trace_enabled(cfg) is False
    monkeypatch.delenv("SUNDAY_TRACE")
    assert config.trace_enabled(cfg) is False


def test_a_write_argument_is_not_dumped_whole_into_the_margin():
    """`text=` on a write is the entire new file. The trace is a margin note,
    not a copy of the payload."""
    line = trace.tool_started("write_file", {"path": "x.txt", "text": "y" * 500}, "local")
    assert len(line.text) < 200
    assert "..." in line.text


def test_the_query_that_left_the_machine_is_narrated(cfg, monkeypatch):
    """The one line in the trace about what actually crossed the airlock. It
    was written and never called, so the narration was silent on the only step
    that leaves the machine."""
    from sunday import web
    from sunday.agent.llm import Reply, ToolCall
    from sunday.runtime import EXTERNAL_TOOL

    monkeypatch.setattr(web, "run", lambda q, budget=None: web.WebResult("public text", hops=1))

    agent = Scripted(
        [Reply(tool_calls=[ToolCall(EXTERNAL_TOOL, {"intent": "gold news"})])],
        query="gold news",
    )
    events: list[dict] = []
    Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "what is the gold news", on_event=events.append
    )

    said = [e["text"] for e in _traces(events, "tools")]
    assert any("gold news" in t and "left this machine" in t for t in said), said

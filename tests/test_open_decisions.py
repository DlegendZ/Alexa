"""The four things the context log listed as open, and what closed them.

Three were fixable in code. The fourth -- the token budget -- is a constraint
rather than a bug, and the test that guards it already lives in test_memory.
"""

from __future__ import annotations

from sunday import airlock, config, stream
from sunday.tools import files


# -- 1. the intent hint, the airlock's weak seam ----------------------------


def test_a_hint_may_reorder_cleared_words():
    """Selecting and reordering what was already crossing is the whole job."""
    kept, dropped = airlock.vet_intent(
        "current gold price forecast", "What is the gold price forecast for June?"
    )
    assert dropped == 0
    assert "gold" in kept and "forecast" in kept


def test_a_hint_cannot_introduce_a_word_that_was_never_cleared():
    """The hole. The hint is written by the agent, which has seen the private
    half -- so "the query writer cannot leak" was true while "the thing that
    writes the hint cannot leak" was only a hope."""
    kept, dropped = airlock.vet_intent(
        "gold price against my sell target of 4600",
        "What is the gold price today?",
    )
    assert "4600" not in kept
    assert "sell" not in kept.lower()
    assert "target" not in kept.lower()
    assert dropped >= 3
    assert "gold" in kept  # what was cleared still crosses


def test_a_number_is_one_word_not_several_harmless_ones():
    """`4,600` split on the comma would be two three-digit fragments, each of
    which looks like nothing on its own."""
    kept, _ = airlock.vet_intent("target 4,600 now", "gold price")
    assert "4,600" not in kept and "600" not in kept


def test_a_wholly_invented_hint_leaves_the_query_to_the_user_words(cfg):
    kept, dropped = airlock.vet_intent("landlord Pak Yusuf rent", "gold price")
    assert kept == ""
    assert dropped == 4


def test_the_vetting_runs_against_exactly_what_the_prompt_will_contain(cfg, monkeypatch):
    """Not a wider notion of "public", which would drift out of step."""
    from sunday.agent.llm import Reply
    from sunday.state import Result

    seen = {}

    class Agent:
        def chat(self, messages, **kwargs):
            seen["prompt"] = messages[1]["content"]
            return Reply(content="gold price")

    state = {
        "task": "what is gold doing",
        "tool_results": [Result("get_asset_price", {}, "Gold (XAU): $4,411", "public")],
    }
    cleared = airlock.compose(Agent(), state, "gold against my 7777 sell target")  # type: ignore[arg-type]

    assert "7777" not in seen["prompt"]
    assert "sell" not in seen["prompt"].lower().split("what is gold doing")[-1]
    assert cleared.intent_words_dropped >= 2


# -- 2. the roots had no names ----------------------------------------------


def test_a_bare_path_still_works_and_takes_the_folder_name(cfg):
    cfg.files.roots = ["C:/Users/User/Documents/Sunday", "E:/Work/Sunday"]
    labels = [r.label for r in cfg.files.entries()]
    assert labels == ["sunday", "sunday"]  # honest: both folders are called Sunday


def test_a_labelled_root_is_what_the_user_calls_it(cfg):
    cfg.files.roots = [
        {"label": "documents", "path": "C:/Users/User/Documents/Sunday"},
        {"label": "work", "path": "E:/Work/Sunday"},
    ]
    entries = cfg.files.entries()
    assert [r.label for r in entries] == ["documents", "work"]
    assert cfg.files.paths() == [
        "C:/Users/User/Documents/Sunday",
        "E:/Work/Sunday",
    ]


def test_the_label_resolves_as_a_path(sandbox, cfg):
    """"Move it to work" is a path the model can actually pass."""
    cfg.files.roots = [{"label": "work", "path": str(sandbox)}]
    (sandbox / "a.txt").write_text("x", encoding="utf-8")
    assert files.resolve("work") == sandbox.resolve()
    assert files.resolve("work/a.txt") == (sandbox / "a.txt").resolve()
    assert files.read_file("work/a.txt") == "x"


def test_the_words_around_a_label_are_ignored(sandbox, cfg):
    cfg.files.roots = [{"label": "work", "path": str(sandbox)}]
    for spoken in ("the work folder", "work folder", "my work directory", "WORK"):
        assert files.resolve(spoken) == sandbox.resolve(), spoken


def test_an_unknown_label_is_not_quietly_treated_as_a_root(sandbox, cfg):
    """It falls through to ordinary relative-path handling, which reads it as
    a name inside a root -- not as a root of its own."""
    cfg.files.roots = [{"label": "work", "path": str(sandbox)}]
    assert files.resolve("photos") == (sandbox / "photos").resolve()
    assert files.list_dir("photos").startswith("error: no such directory")


# -- 3. markdown reaching a reply that gets spoken --------------------------


def test_backticks_and_asterisks_never_reach_a_sink():
    out: list[str] = []
    text = stream.fork(
        iter(["The file ", "`gold", ".txt` has ", "**moved**."]),
        on_token=out.append,
    )
    assert text == "The file gold.txt has moved."
    assert "`" not in "".join(out) and "*" not in "".join(out)


def test_a_bullet_at_the_start_of_a_line_goes_too():
    text = stream.fork(iter(["Files:\n", "- a.txt\n", "- b.txt\n", "1. c.txt"]))
    assert text == "Files:\na.txt\nb.txt\nc.txt"


def test_a_marker_split_across_two_tokens_is_still_caught():
    """The token boundary can fall between the newline and the dash."""
    text = stream.fork(iter(["Files:\n", "-", " a.txt\n", "-", " b.txt"]))
    assert "- " not in text
    assert text.endswith("b.txt")


def test_underscores_and_hyphens_inside_words_are_left_alone():
    """Half the filenames on the machine have an underscore in them, and a
    hyphen mid-sentence is punctuation, not a bullet."""
    text = stream.fork(iter(["Wrote my_notes-2026.txt -- all of it."]))
    assert text == "Wrote my_notes-2026.txt -- all of it."


def test_what_is_shown_spoken_and_filed_is_the_same_text():
    """Cleaning in one sink and not the others is how a transcript ends up
    disagreeing with what the person heard."""
    tokens: list[str] = []
    spoken: list[str] = []
    text = stream.fork(
        iter(["`gold.txt` moved. ", "**Done**."]),
        on_token=tokens.append,
        on_sentence=spoken.append,
    )
    assert "".join(tokens) == text
    assert " ".join(spoken) == text.strip()
    assert "`" not in text and "*" not in text


# -- several jobs in one message --------------------------------------------


def test_three_jobs_in_one_message_run_across_rounds(cfg, tmp_path):
    """The loop is not one call per turn. Each round may ask for more, and the
    turn ends when the model stops asking -- not when the first tool returns."""
    from sunday.agent.llm import Reply, ToolCall
    from sunday.runtime import Runtime
    from tests.test_review_fixes import NoMemory, Scripted

    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [{"label": "work", "path": str(root)}]

    agent = Scripted(
        [
            Reply(tool_calls=[ToolCall("write_file", {"path": str(root / "a.txt"), "text": "one"})]),
            Reply(tool_calls=[ToolCall("copy_file", {"source": str(root / "a.txt"), "destination": str(root / "b.txt")})]),
            Reply(tool_calls=[ToolCall("list_dir", {"path": "work"})]),
            Reply(content="Done."),
        ]
    )
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "write a, copy it to b, then list the folder"
    )

    assert [r.tool for r in state["tool_results"]] == [
        "write_file",
        "copy_file",
        "list_dir",
    ]
    assert state["tool_calls"] == 3
    assert (root / "b.txt").read_text(encoding="utf-8") == "one"
    assert "a.txt" in state["tool_results"][2].content


def test_two_jobs_in_one_round_both_run(cfg, tmp_path):
    """A single reply may declare several calls, and each one is answered."""
    from sunday.agent.llm import Reply, ToolCall
    from sunday.runtime import Runtime
    from tests.test_review_fixes import NoMemory, Scripted

    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    (root / "a.txt").write_text("one", encoding="utf-8")
    (root / "b.txt").write_text("two", encoding="utf-8")

    agent = Scripted(
        [
            Reply(
                tool_calls=[
                    ToolCall("read_file", {"path": str(root / "a.txt")}),
                    ToolCall("read_file", {"path": str(root / "b.txt")}),
                ]
            ),
            Reply(content="Both read."),
        ]
    )
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn("read both")  # type: ignore[arg-type]

    assert [r.content for r in state["tool_results"]] == ["one", "two"]


def test_a_repeated_failing_call_is_refused_without_running(cfg, tmp_path):
    """Asked to move a file to a folder that did not exist, the model sent the
    identical call on rounds 1, 2, 3, 5 and 7 -- spending the whole cap on one
    call that could never work. RETRY_HINT says "do not send the same call
    again unchanged" in as many words, and it sent it again unchanged. A prompt
    cannot enforce a loop bound."""
    from sunday.agent.llm import Reply, ToolCall
    from sunday.runtime import REPEATED, Runtime
    from tests.test_review_fixes import NoMemory, Scripted

    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]

    missing = ToolCall("read_file", {"path": str(root / "gone.txt")})
    agent = Scripted(
        [
            Reply(tool_calls=[missing]),
            Reply(tool_calls=[missing]),
            Reply(tool_calls=[missing]),
            Reply(content="I could not read it."),
        ]
    )
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn("read it")  # type: ignore[arg-type]

    contents = [r.content for r in state["tool_results"]]
    assert contents[0].startswith("error: no such file")  # the real attempt ran
    assert contents[1] == REPEATED  # the repeat did not
    assert state["unresolved"]  # the third ends the turn rather than looping


def test_a_repeated_call_that_worked_is_left_alone(cfg, tmp_path):
    """Wasteful, not pathological -- and two of this codebase's guarantees are
    tested by issuing the same successful call twice."""
    from sunday.agent.llm import Reply, ToolCall
    from sunday.runtime import REPEATED, Runtime
    from tests.test_review_fixes import NoMemory, Scripted

    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    (root / "a.txt").write_text("hello", encoding="utf-8")

    call = ToolCall("read_file", {"path": str(root / "a.txt")})
    agent = Scripted([Reply(tool_calls=[call, call]), Reply(content="ok")])
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn("read it twice")  # type: ignore[arg-type]

    assert [r.content for r in state["tool_results"]] == ["hello", "hello"]
    assert REPEATED not in [r.content for r in state["tool_results"]]


def test_a_turn_with_nothing_to_say_says_so(cfg):
    """Spending the budget on repeats left the model with no words at all, and
    a blank reply reads as a crash."""
    from sunday.agent.llm import Reply
    from sunday.runtime import EMPTY_REPLY, Runtime
    from tests.test_review_fixes import NoMemory

    class Mute:
        def chat(self, messages, **kwargs):
            return Reply(content="")

        def stream(self, messages, *, think=False):
            return iter(())

        def preflight(self):
            return None

    spoken: list[str] = []
    state = Runtime(cfg, agent=Mute(), memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "do the thing", on_sentence=spoken.append
    )

    assert state["final_response"] == EMPTY_REPLY
    assert spoken == [EMPTY_REPLY]  # the speech sink hears it too, not silence

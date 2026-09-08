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

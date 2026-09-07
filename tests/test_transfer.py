"""Moving and copying inside the sandbox.

Two ends instead of one, so every check has to run twice -- and the source
check is the one that carries weight. The deny overlay works on paths, so
copying `.env` to `notes.txt` would leave the same bytes somewhere the overlay
says nothing about: the next read would be ordinary, the turn would stay clean,
and the web door would stay open. A copy is a laundering operation on the one
thing the overlay protects.
"""

from __future__ import annotations

from sunday.agent.llm import Reply, ToolCall
from sunday.runtime import WRITE_DECLINED, WRITE_UNATTENDED, Runtime
from sunday.tools import files

from tests.test_review_fixes import NoMemory, Scripted


def _call(tool, source, destination):
    return ToolCall(tool, {"source": str(source), "destination": str(destination)})


# -- the sandbox ------------------------------------------------------------


def test_copies_within_a_root(sandbox):
    (sandbox / "gold.txt").write_text("sell at 7777", encoding="utf-8")
    out = files.copy_file(str(sandbox / "gold.txt"), str(sandbox / "gold-backup.txt"))
    assert out.startswith("copied ")
    assert (sandbox / "gold-backup.txt").read_text(encoding="utf-8") == "sell at 7777"
    assert (sandbox / "gold.txt").exists()  # a copy leaves the original


def test_moves_within_a_root(sandbox):
    (sandbox / "gold.txt").write_text("sell at 7777", encoding="utf-8")
    out = files.move_file(str(sandbox / "gold.txt"), str(sandbox / "archive.txt"))
    assert out.startswith("moved ")
    assert (sandbox / "archive.txt").read_text(encoding="utf-8") == "sell at 7777"
    assert not (sandbox / "gold.txt").exists()


def test_a_folder_destination_means_into_it_keeping_the_name(sandbox):
    """What a person means by copy-paste, and what the model passes when it
    repeats the folder back."""
    (sandbox / "gold.txt").write_text("sell at 7777", encoding="utf-8")
    (sandbox / "archive").mkdir()
    files.copy_file(str(sandbox / "gold.txt"), str(sandbox / "archive"))
    assert (sandbox / "archive" / "gold.txt").read_text(encoding="utf-8") == "sell at 7777"


def test_a_move_can_rename_in_the_same_call(sandbox):
    (sandbox / "a.txt").write_text("x", encoding="utf-8")
    files.move_file(str(sandbox / "a.txt"), str(sandbox / "deep" / "b.txt"))
    assert (sandbox / "deep" / "b.txt").read_text(encoding="utf-8") == "x"


def test_refuses_a_source_outside_every_root(sandbox, tmp_path):
    outside = tmp_path / "theirs.txt"
    outside.write_text("not yours", encoding="utf-8")
    assert files.copy_file(str(outside), str(sandbox / "mine.txt")) == files.REFUSED
    assert not (sandbox / "mine.txt").exists()


def test_refuses_a_destination_outside_every_root(sandbox, tmp_path):
    """The interesting direction: this is how a file would leave the sandbox."""
    (sandbox / "gold.txt").write_text("sell at 7777", encoding="utf-8")
    escape = tmp_path / "escaped.txt"
    assert files.copy_file(str(sandbox / "gold.txt"), str(escape)) == files.REFUSED
    assert not escape.exists()


def test_refuses_a_credential_source(sandbox):
    """The laundering case. Refusing at the source is what stops it: taint
    would not help, because the copy's own path is not on the list."""
    (sandbox / ".env").write_text("DEEPSEEK_API_KEY=abc", encoding="utf-8")
    out = files.copy_file(str(sandbox / ".env"), str(sandbox / "notes.txt"))
    assert "credential" in out
    assert not (sandbox / "notes.txt").exists()


def test_refuses_a_credential_destination(sandbox):
    (sandbox / "junk.txt").write_text("junk", encoding="utf-8")
    (sandbox / ".env").write_text("DEEPSEEK_API_KEY=abc", encoding="utf-8")
    out = files.move_file(str(sandbox / "junk.txt"), str(sandbox / ".env"))
    assert "credential" in out
    assert (sandbox / ".env").read_text(encoding="utf-8") == "DEEPSEEK_API_KEY=abc"


def test_refuses_a_folder_as_the_source(sandbox):
    (sandbox / "sub").mkdir()
    out = files.copy_file(str(sandbox / "sub"), str(sandbox / "sub2"))
    assert "folder" in out
    assert not (sandbox / "sub2").exists()


def test_a_missing_source_is_an_error_not_a_refusal(sandbox):
    out = files.move_file(str(sandbox / "gone.txt"), str(sandbox / "there.txt"))
    assert out.startswith("error: no such file")


def test_copying_a_file_onto_itself_is_refused(sandbox):
    """shutil would truncate it to nothing, which is a data-loss bug wearing
    the clothes of a no-op."""
    (sandbox / "a.txt").write_text("keep me", encoding="utf-8")
    out = files.copy_file(str(sandbox / "a.txt"), str(sandbox / "a.txt"))
    assert "same file" in out
    assert (sandbox / "a.txt").read_text(encoding="utf-8") == "keep me"


def test_the_same_file_check_survives_a_folder_destination(sandbox):
    (sandbox / "a.txt").write_text("keep me", encoding="utf-8")
    out = files.copy_file(str(sandbox / "a.txt"), str(sandbox))
    assert "same file" in out
    assert (sandbox / "a.txt").read_text(encoding="utf-8") == "keep me"


# -- the confirmation -------------------------------------------------------


def _root(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    return root


def test_landing_on_nothing_does_not_ask(cfg, tmp_path):
    root = _root(cfg, tmp_path)
    (root / "a.txt").write_text("x", encoding="utf-8")

    asked = []
    agent = Scripted([Reply(tool_calls=[_call("copy_file", root / "a.txt", root / "b.txt")])])
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "copy it", on_confirm=lambda r: asked.append(r) or True
    )

    assert asked == []
    assert (root / "b.txt").read_text(encoding="utf-8") == "x"
    assert state["tool_results"][0].ok is True


def test_landing_on_a_file_asks_about_the_destination(cfg, tmp_path):
    """The source survives a copy and is one call away after a move. What
    cannot be undone is what was already sitting at the destination."""
    root = _root(cfg, tmp_path)
    (root / "a.txt").write_text("new", encoding="utf-8")
    (root / "b.txt").write_text("old", encoding="utf-8")

    asked = []
    agent = Scripted([Reply(tool_calls=[_call("move_file", root / "a.txt", root / "b.txt")])])
    Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "move it over", on_confirm=lambda r: asked.append(r) or True
    )

    assert len(asked) == 1
    assert asked[0].path == str(root / "b.txt")  # the destination, not the source
    assert asked[0].action == "overwrite"
    assert (root / "b.txt").read_text(encoding="utf-8") == "new"
    assert not (root / "a.txt").exists()


def test_saying_no_leaves_both_files_alone(cfg, tmp_path):
    root = _root(cfg, tmp_path)
    (root / "a.txt").write_text("new", encoding="utf-8")
    (root / "b.txt").write_text("old", encoding="utf-8")

    agent = Scripted([Reply(tool_calls=[_call("move_file", root / "a.txt", root / "b.txt")])])
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "move it over", on_confirm=lambda r: False
    )

    assert (root / "a.txt").read_text(encoding="utf-8") == "new"
    assert (root / "b.txt").read_text(encoding="utf-8") == "old"
    assert state["tool_results"][0].content == WRITE_DECLINED


def test_with_nobody_attached_it_fails_closed(cfg, tmp_path):
    root = _root(cfg, tmp_path)
    (root / "a.txt").write_text("new", encoding="utf-8")
    (root / "b.txt").write_text("old", encoding="utf-8")

    agent = Scripted([Reply(tool_calls=[_call("copy_file", root / "a.txt", root / "b.txt")])])
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "copy it over"
    )

    assert (root / "b.txt").read_text(encoding="utf-8") == "old"
    assert state["tool_results"][0].content == WRITE_UNATTENDED


def test_a_yes_does_not_widen_the_sandbox(cfg, tmp_path):
    root = _root(cfg, tmp_path)
    (root / "a.txt").write_text("new", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("not yours", encoding="utf-8")

    asked = []
    agent = Scripted([Reply(tool_calls=[_call("copy_file", root / "a.txt", outside)])])
    Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "copy it out", on_confirm=lambda r: asked.append(r) or True
    )

    assert asked == []  # the sandbox answered first
    assert outside.read_text(encoding="utf-8") == "not yours"


def test_a_credential_source_is_refused_and_leaves_the_door_open(cfg, tmp_path):
    """A refused reach is not a read, so it does not bolt the web door -- but
    nothing was laundered either, which is the point."""
    root = _root(cfg, tmp_path)
    (root / ".env").write_text("KEY=1", encoding="utf-8")

    agent = Scripted([Reply(tool_calls=[_call("copy_file", root / ".env", root / "n.txt")])])
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "copy my env", on_confirm=lambda r: True
    )

    assert not (root / "n.txt").exists()
    assert state["tool_results"][0].ok is False
    assert state["tainted"] is False

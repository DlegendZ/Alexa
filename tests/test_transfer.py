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
    (sandbox / "deep").mkdir()
    files.move_file(str(sandbox / "a.txt"), str(sandbox / "deep" / "b.txt"))
    assert (sandbox / "deep" / "b.txt").read_text(encoding="utf-8") == "x"


def test_refuses_a_source_outside_every_root(sandbox, tmp_path):
    outside = tmp_path / "theirs.txt"
    outside.write_text("not yours", encoding="utf-8")
    assert files.copy_file(str(outside), str(sandbox / "mine.txt")) == files.refused()
    assert not (sandbox / "mine.txt").exists()


def test_refuses_a_destination_outside_every_root(sandbox, tmp_path):
    """The interesting direction: this is how a file would leave the sandbox."""
    (sandbox / "gold.txt").write_text("sell at 7777", encoding="utf-8")
    escape = tmp_path / "escaped.txt"
    assert files.copy_file(str(sandbox / "gold.txt"), str(escape)) == files.refused()
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


# -- the fast path ----------------------------------------------------------


def test_a_move_inside_a_root_never_copies_the_bytes(sandbox, monkeypatch):
    """A rename touches directory entries, nothing else. It costs the same for
    a 2 KB note and a 2 GB recording, and there is no instant where the file
    exists twice or not at all. Blow up if anything reads the file."""

    def explode(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("the bytes were copied for a same-volume move")

    monkeypatch.setattr("shutil.copy2", explode)

    (sandbox / "big.bin").write_bytes(b"x" * 4096)
    out = files.move_file(str(sandbox / "big.bin"), str(sandbox / "moved.bin"))
    assert out.startswith("moved ")
    assert (sandbox / "moved.bin").read_bytes() == b"x" * 4096
    assert not (sandbox / "big.bin").exists()


def test_a_move_onto_an_existing_file_still_replaces_it(sandbox):
    """`os.replace`, not `os.rename`: rename overwrites on POSIX and raises on
    Windows, and this needs one behaviour. The runtime has already asked."""
    (sandbox / "a.txt").write_text("new", encoding="utf-8")
    (sandbox / "b.txt").write_text("old", encoding="utf-8")
    files.move_file(str(sandbox / "a.txt"), str(sandbox / "b.txt"))
    assert (sandbox / "b.txt").read_text(encoding="utf-8") == "new"
    assert not (sandbox / "a.txt").exists()


def test_a_cross_drive_move_falls_back_to_copying(sandbox, monkeypatch):
    """No filesystem renames across volumes, so that case pays what it has to.
    Faked here, because the test suite cannot count on a second drive."""
    import errno as errno_module

    def refuse(*args, **kwargs):
        raise OSError(errno_module.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr("os.replace", refuse)

    (sandbox / "a.txt").write_text("travelling", encoding="utf-8")
    (sandbox / "far").mkdir()
    out = files.move_file(str(sandbox / "a.txt"), str(sandbox / "far" / "b.txt"))
    assert out.startswith("moved ")
    assert (sandbox / "far" / "b.txt").read_text(encoding="utf-8") == "travelling"
    assert not (sandbox / "a.txt").exists()


def test_a_real_rename_failure_is_not_mistaken_for_a_cross_drive_one(sandbox, monkeypatch):
    """Only a cross-device error may fall back. Anything else is a failure and
    has to be reported as one, or a permissions problem turns into a silent
    copy that leaves the original behind."""

    def refuse(*args, **kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr("os.replace", refuse)

    (sandbox / "a.txt").write_text("stay", encoding="utf-8")
    out = files.move_file(str(sandbox / "a.txt"), str(sandbox / "b.txt"))
    assert out.startswith("error: could not move")
    assert (sandbox / "a.txt").read_text(encoding="utf-8") == "stay"
    assert not (sandbox / "b.txt").exists()


def test_a_hard_linked_alias_counts_as_the_same_file(sandbox):
    """Two real names for one set of bytes. The string compare cannot see it,
    and `copy2` truncates the destination before it reads anything -- so a copy
    onto an alias of the source empties the file and then copies the nothing."""
    import os as os_module

    source = sandbox / "a.txt"
    source.write_text("precious", encoding="utf-8")
    alias = sandbox / "alias.txt"
    try:
        os_module.link(source, alias)
    except (OSError, NotImplementedError, AttributeError):
        import pytest

        pytest.skip("no hard links on this filesystem")

    out = files.copy_file(str(source), str(alias))
    assert "same file" in out
    assert source.read_text(encoding="utf-8") == "precious"


def test_a_trailing_slash_means_a_folder_even_before_it_exists(sandbox):
    """The model writes `E:/Work/Sunday/archive/` when it means a folder.
    Without this that becomes a *file* named `archive`, which then blocks the
    folder from ever being created -- a mess nobody thinks to look for."""
    (sandbox / "gold.txt").write_text("sell at 7777", encoding="utf-8")
    (sandbox / "archive").mkdir()
    out = files.move_file(str(sandbox / "gold.txt"), str(sandbox / "archive") + "/")
    assert out.startswith("moved ")
    assert (sandbox / "archive").is_dir()
    assert (sandbox / "archive" / "gold.txt").read_text(encoding="utf-8") == "sell at 7777"


def test_an_extensionless_destination_is_read_as_a_folder(sandbox):
    """`gold.txt` -> `documents` is somebody naming a folder. Taken as a file
    name it silently creates a file called `documents`, the original is gone,
    and the next turn goes hunting for it -- which is exactly what happened,
    three runs in a row, when the model guessed at "the documents folder"."""
    (sandbox / "gold.txt").write_text("sell at 7777", encoding="utf-8")
    (sandbox / "documents").mkdir()
    files.move_file(str(sandbox / "gold.txt"), str(sandbox / "documents"))
    assert (sandbox / "documents" / "gold.txt").exists()
    assert not (sandbox / "documents").is_file()


def test_a_destination_folder_that_does_not_exist_is_reported_not_created(sandbox):
    """Sunday does not create folders to make a move fit. Asked to move a file
    to "the documents folder", the model invented one three separate ways --
    as a file, as a folder, then as `documents/folder` -- and each time the
    original landed somewhere the next turn could not find."""
    (sandbox / "gold.txt").write_text("sell at 7777", encoding="utf-8")
    for guess in ("nope/deep", "documents", "documents/folder"):
        out = files.move_file(str(sandbox / "gold.txt"), str(sandbox / guess))
        assert out.startswith("error: there is no folder at"), guess
        assert (sandbox / "gold.txt").exists()
    assert not (sandbox / "documents").exists()
    assert not (sandbox / "nope").exists()

"""Milestone 2: it reads and writes inside the roots, and refuses outside them."""

from __future__ import annotations

import os

import pytest

from sunday.tools import files


def test_reads_a_file_inside_a_root(sandbox):
    (sandbox / "note.txt").write_text("sell at 3000", encoding="utf-8")
    assert files.read_file(str(sandbox / "note.txt")) == "sell at 3000"


def test_refuses_a_path_outside_every_root(sandbox, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("not yours", encoding="utf-8")
    assert files.read_file(str(outside)) == files.refused()


def test_refuses_traversal_out_of_a_root(sandbox, tmp_path):
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    traversal = str(sandbox / ".." / "secret.txt")
    assert files.read_file(traversal) == files.refused()


def test_refuses_before_touching_the_disk(sandbox, monkeypatch):
    """The check runs before any read, so a refused path never reaches it."""
    from pathlib import Path

    def explode(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("the disk was touched for a refused path")

    monkeypatch.setattr(Path, "read_bytes", explode)
    assert files.read_file("C:/Windows/System32/config/SAM") == files.refused()


def test_case_insensitive_root_match_on_windows(sandbox):
    if os.name != "nt":
        pytest.skip("Windows path casing")
    (sandbox / "note.txt").write_text("hello", encoding="utf-8")
    upper = str(sandbox / "note.txt").upper()
    assert files.read_file(upper) == "hello"


def test_read_is_truncated_at_the_cap(sandbox):
    (sandbox / "big.txt").write_text("x" * 500, encoding="utf-8")
    out = files.read_file(str(sandbox / "big.txt"))
    assert out.endswith(files.TRUNCATED)
    assert len(out) == 200 + len(files.TRUNCATED)


def test_write_refuses_a_folder_that_does_not_exist(sandbox):
    """This test used to assert the opposite, and pinned the one place the
    assistant still built folders on a guess. Copy and move already refused;
    write did not, so the rule held for two tools out of three."""
    target = sandbox / "deep" / "new.txt"
    out = files.write_file(str(target), "hello")
    assert out.startswith("error: there is no folder at")
    assert "do not create" in out
    assert not (sandbox / "deep").exists()


def test_write_into_a_folder_that_exists_still_works(sandbox):
    (sandbox / "deep").mkdir()
    target = sandbox / "deep" / "new.txt"
    assert files.write_file(str(target), "hello").startswith("wrote 5 characters")
    assert target.read_text(encoding="utf-8") == "hello"


def test_write_refuses_outside_the_root(sandbox, tmp_path):
    target = tmp_path / "escape.txt"
    assert files.write_file(str(target), "hello") == files.refused()
    assert not target.exists()


def test_write_refuses_over_a_credential_path(sandbox):
    target = sandbox / ".env"
    assert "credential" in files.write_file(str(target), "KEY=1")
    assert not target.exists()


def test_list_dir_gives_names_and_sizes_never_contents(sandbox):
    (sandbox / "a.txt").write_text("12345", encoding="utf-8")
    (sandbox / "sub").mkdir()
    out = files.list_dir(str(sandbox))
    assert "sub/" in out
    assert "a.txt  5 bytes" in out
    assert "12345" not in out


def test_missing_file_is_an_error_not_a_refusal(sandbox):
    out = files.read_file(str(sandbox / "gone.txt"))
    assert out.startswith("error: no such file")


def test_errors_arrive_as_tool_results_not_exceptions(sandbox):
    from sunday import tools

    tool = tools.get("read_file")
    assert tool is not None
    assert tool.invoke({"path": ""}) == files.refused()
    assert tool.invoke({"wrong": "arg"}).startswith("error: bad arguments")


def test_a_relative_path_is_tried_against_the_roots_not_the_cwd(sandbox):
    """The model cannot know which folder Sunday was launched from, so a bare
    `notes/plan.txt` used to land at `<cwd>/notes/plan.txt` -- a path that does
    not exist, refused with "no such file", which reads to the user as their
    folder being missing rather than as a path they never asked for."""
    (sandbox / "notes").mkdir()
    (sandbox / "notes" / "plan.txt").write_text("sell at 3000", encoding="utf-8")
    assert files.read_file("notes/plan.txt") == "sell at 3000"


def test_a_relative_path_still_cannot_leave_the_roots(sandbox, tmp_path):
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    assert files.read_file("../secret.txt") == files.refused()


def test_a_relative_path_to_a_file_that_does_not_exist_yet_still_writes(sandbox):
    """The relative-path reading has to work for a file being created, not
    only for one already there -- resolve() has nothing on disk to match."""
    (sandbox / "fresh").mkdir()
    out = files.write_file("fresh/new.txt", "hello")
    assert out.startswith("wrote 5 characters")
    assert (sandbox / "fresh" / "new.txt").read_text(encoding="utf-8") == "hello"


def every_refusal(sandbox, tmp_path):
    """Every string in files.py that starts with `refused:`, produced by
    actually taking the path that produces it.

    Enumerated from the rule rather than from the bug reports. The list used to
    be four incidents long, and the one it happened to leave out was the
    credential *source* -- the one refusal in this file that exists because
    taint cannot fix a laundered copy after the fact.
    """
    (sandbox / ".env").write_text("KEY=1", encoding="utf-8")
    (sandbox / "junk.txt").write_text("junk", encoding="utf-8")
    (sandbox / "near").mkdir(exist_ok=True)
    (tmp_path / "outside.txt").write_text("not yours", encoding="utf-8")

    return {
        # the sandbox, both shapes of the refusal it hands back
        "outside a root": files.read_file(str(tmp_path / "outside.txt")),
        "one level above a root": files.list_dir(str(sandbox.parent)),
        "traversal": files.read_file(str(sandbox / ".." / "outside.txt")),
        # credential paths, at each of the four ends that can name one
        "write over a credential": files.write_file(str(sandbox / ".env"), "K=2"),
        "delete a credential": files.delete_file(str(sandbox / ".env")),
        "copy from a credential": files.copy_file(
            str(sandbox / ".env"), str(sandbox / "leak.txt")
        ),
        "move from a credential": files.move_file(
            str(sandbox / ".env"), str(sandbox / "leak.txt")
        ),
        "copy onto a credential": files.copy_file(
            str(sandbox / "junk.txt"), str(sandbox / ".env")
        ),
        "move onto a credential": files.move_file(
            str(sandbox / "junk.txt"), str(sandbox / ".env")
        ),
    }


def test_every_refusal_tells_the_model_what_to_say(sandbox, tmp_path):
    """A refusal string is a script, not a status code. Two of them were bare
    status -- "will not write over a credential file" -- and a bare refusal is
    how `path is outside the configured roots` once got relayed to the user as
    "C: isn't mounted"."""
    for why, out in every_refusal(sandbox, tmp_path).items():
        assert out.startswith("refused:"), (why, out)
        assert "tell the user" in out.lower(), (why, out)


def test_no_refusal_writes_down_a_name(sandbox, tmp_path):
    """Note 83: the assistant is called whatever `[assistant] name` says, and
    nothing may hard-code it.

    These strings are scripts the model relays, and the 2b relays the wording
    it was handed -- so "Sunday is not allowed into that folder" is a sentence
    the user hears in a voice that calls itself Alexa. They are written in the
    second person instead, which cannot go stale because there is no name in
    it. The temp root has no "sunday" in its path, so a hit here is prose and
    not an interpolated path.
    """
    assert "sunday" not in str(sandbox).lower()
    for why, out in every_refusal(sandbox, tmp_path).items():
        assert "sunday" not in out.lower(), (why, out)

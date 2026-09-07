"""Deleting a file: the sandbox half, and the confirmation half.

Overwriting asks only when there is something to lose. Deleting asks always,
because there is no arrangement of the arguments where it does not destroy
something.
"""

from __future__ import annotations

from sunday.agent.llm import Reply, ToolCall
from sunday.runtime import DELETE_DECLINED, DELETE_UNATTENDED, Runtime
from sunday.tools import files

from tests.test_review_fixes import NoMemory, Scripted


def _delete_call(path):
    return ToolCall("delete_file", {"path": str(path)})


# -- the sandbox ------------------------------------------------------------


def test_deletes_a_file_inside_a_root(sandbox):
    target = sandbox / "scratch.txt"
    target.write_text("throwaway", encoding="utf-8")
    assert files.delete_file(str(target)).startswith("deleted ")
    assert not target.exists()


def test_refuses_a_path_outside_every_root(sandbox, tmp_path):
    outside = tmp_path / "theirs.txt"
    outside.write_text("not yours", encoding="utf-8")
    assert files.delete_file(str(outside)) == files.REFUSED
    assert outside.exists()


def test_refuses_a_credential_file_even_inside_a_root(sandbox):
    target = sandbox / ".env"
    target.write_text("KEY=1", encoding="utf-8")
    assert "credential" in files.delete_file(str(target))
    assert target.exists()


def test_refuses_a_folder_outright(sandbox):
    (sandbox / "sub").mkdir()
    out = files.delete_file(str(sandbox / "sub"))
    assert "folder" in out
    assert (sandbox / "sub").exists()


def test_a_missing_file_is_an_error_not_a_silent_success(sandbox):
    out = files.delete_file(str(sandbox / "gone.txt"))
    assert out.startswith("error: no such file")


# -- the confirmation -------------------------------------------------------


def test_deleting_always_asks_even_though_writing_does_not(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    target = root / "scratch.txt"
    target.write_text("throwaway", encoding="utf-8")

    asked = []
    agent = Scripted([Reply(tool_calls=[_delete_call(target)])])
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "delete that", on_confirm=lambda r: asked.append(r) or True
    )

    assert len(asked) == 1
    assert asked[0].action == "delete"
    assert asked[0].path == str(target)
    assert "cannot be undone" in asked[0].question
    assert not target.exists()
    assert state["tool_results"][0].ok is True


def test_saying_no_leaves_the_file_exactly_where_it_was(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    target = root / "scratch.txt"
    target.write_text("throwaway", encoding="utf-8")

    agent = Scripted([Reply(tool_calls=[_delete_call(target)])])
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "delete that", on_confirm=lambda r: False
    )

    assert target.read_text(encoding="utf-8") == "throwaway"
    assert state["tool_results"][0].content == DELETE_DECLINED
    assert state["tool_results"][0].ok is False
    assert any("still there" in n for n in state["notices"])  # type: ignore[typeddict-item]


def test_with_nobody_attached_the_delete_fails_closed(cfg, tmp_path):
    """Silence is not consent, and this is the case where that matters most."""
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    target = root / "scratch.txt"
    target.write_text("throwaway", encoding="utf-8")

    agent = Scripted([Reply(tool_calls=[_delete_call(target)])])
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "delete that"
    )

    assert target.exists()
    assert state["tool_results"][0].content == DELETE_UNATTENDED
    assert state["tool_results"][0].ok is False


def test_a_yes_does_not_widen_what_the_sandbox_allows(cfg, tmp_path):
    """Confirmation widens what the user may allow, never what the roots do."""
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    outside = tmp_path / "theirs.txt"
    outside.write_text("not yours", encoding="utf-8")

    asked = []
    agent = Scripted([Reply(tool_calls=[_delete_call(outside)])])
    Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "delete that", on_confirm=lambda r: asked.append(r) or True
    )

    assert asked == []  # the sandbox answered first; nothing to consent to
    assert outside.exists()


def test_a_credential_file_survives_even_a_yes(cfg, tmp_path):
    """The deny overlay outranks consent, exactly as it does for a write. And
    a refused call is not a read, so it does not bolt the web door either --
    reaching for `.env` and being stopped is not the same as having seen it."""
    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    target = root / ".env"
    target.write_text("KEY=1", encoding="utf-8")

    agent = Scripted([Reply(tool_calls=[_delete_call(target)])])
    state = Runtime(cfg, agent=agent, memory=NoMemory()).run_turn(  # type: ignore[arg-type]
        "delete that", on_confirm=lambda r: True
    )

    assert target.read_text(encoding="utf-8") == "KEY=1"
    assert state["tool_results"][0].ok is False
    assert state["tainted"] is False

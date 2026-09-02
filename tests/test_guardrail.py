"""Milestone 3: reading .env taints the turn; a mailed ghp_ token is redacted.

Both proven here rather than by eye, and with them the case the document
singles out as the one people forget: two calls in one model response, where
the second is refused because the first tainted the turn.
"""

from __future__ import annotations

import pytest

from sunday import guardrail
from sunday.agent.llm import Reply, ToolCall
from sunday.runtime import DOOR_SHUT, EXTERNAL_TOOL, Flags, Runtime


# -- source taint ----------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "C:/Users/me/project/.env",
        "C:/Users/me/keys/server.key",
        "C:/Users/me/keys/server.pem",
        "C:/Users/me/.ssh/id_rsa",
        "C:/Users/me/.ssh/config",
        "C:/Users/me/.aws/credentials",
        "C:/Users/me/credentials.json",
        "C:/Users/me/vault.kdbx",
        "C:/Users/me/AppData/Local/Sunday/google_token.json",
    ],
)
def test_credential_paths_are_recognised(cfg, path):
    assert guardrail.is_secret_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "C:/Users/me/Documents/Sunday/gold.txt",
        "C:/Users/me/Documents/environment-notes.md",
        "C:/Users/me/Documents/keynote.txt",
        "C:/Users/me/Documents/my.credentials.summary/report.txt",
    ],
)
def test_ordinary_paths_are_not(cfg, path):
    assert guardrail.is_secret_path(path) is False


def test_backslashes_and_casing_do_not_hide_a_credential_path(cfg):
    assert guardrail.is_secret_path(r"C:\Users\Me\Project\.ENV") is True
    assert guardrail.is_secret_path(r"C:\Users\Me\.SSH\known_hosts") is True


# -- shape match -----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "here is the token ghp_16C7e42F292c6912E7710c838347Ae178B4a",
        "AWS key AKIAIOSFODNN7EXAMPLE in the body",
        "openai sk-proj-abc123def456ghi789jkl",
        "slack xoxb-123456789012-abcdefghijklmnop",
        "jwt eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0",
    ],
)
def test_key_shapes_are_stripped(cfg, text):
    cleaned, count = guardrail.redact(text)
    assert count == 1
    assert guardrail.REDACTED in cleaned
    for token in text.split():
        if len(token) > 20:
            assert token not in cleaned


def test_private_key_block_is_stripped(cfg):
    text = (
        "attached:\n-----BEGIN RSA PRIVATE KEY-----\nMIIEow...lines...\n"
        "-----END RSA PRIVATE KEY-----\nregards"
    )
    cleaned, count = guardrail.redact(text)
    assert count == 1
    assert "MIIEow" not in cleaned
    assert cleaned.startswith("attached:")


def test_ordinary_text_survives_untouched(cfg):
    text = "The invoice is 4460101 and the commit is a1b2c3d4e5f6, meeting at 3pm."
    cleaned, count = guardrail.redact(text)
    assert count == 0
    assert cleaned == text


def test_query_is_capped_at_the_configured_length(cfg):
    cfg.external.query_max_chars = 40
    cleaned, _ = guardrail.scrub_query("gold price analysis " * 20)
    assert len(cleaned) <= 40


# -- the door --------------------------------------------------------------


class FakeAgent:
    """Replays a scripted sequence of model responses."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.bound_names: list[list[str]] = []

    def chat(self, messages, *, tools=None, think=False, max_tokens=None):
        if tools is not None:
            self.bound_names.append([t["function"]["name"] for t in tools])
        return self.replies.pop(0) if self.replies else Reply(content="done")

    def stream(self, messages, *, think=False):
        yield "ok"

    def preflight(self):
        return None


def _runtime(cfg, replies):
    agent = FakeAgent(replies)
    runtime = Runtime(cfg, agent=agent)  # type: ignore[arg-type]
    return runtime, agent


def test_two_calls_in_one_response_and_the_second_is_refused(cfg, tmp_path):
    """The case binding alone cannot cover: both calls were decided while the
    turn was still clean."""
    root = tmp_path / "root"
    root.mkdir()
    (root / ".env").write_text("DEEPSEEK_API_KEY=budi1990", encoding="utf-8")
    cfg.files.roots = [str(root)]

    replies = [
        Reply(
            tool_calls=[
                ToolCall("read_file", {"path": str(root / ".env")}),
                ToolCall(EXTERNAL_TOOL, {"intent": "what is this key for"}),
            ]
        ),
        Reply(content="done"),
    ]
    runtime, _ = _runtime(cfg, replies)
    state = runtime.run_turn("read my .env and look it up")

    assert state["tainted"] is True
    assert state["blocked"] is True
    results = state["tool_results"]
    assert results[1].tool == EXTERNAL_TOOL
    assert results[1].content == DOOR_SHUT
    assert state["unresolved"]


def test_the_door_is_unbound_for_the_next_round(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / ".env").write_text("KEY=1", encoding="utf-8")
    cfg.files.roots = [str(root)]

    replies = [
        Reply(tool_calls=[ToolCall("read_file", {"path": str(root / ".env")})]),
        Reply(content="done"),
    ]
    runtime, agent = _runtime(cfg, replies)
    runtime.run_turn("read my .env")

    assert EXTERNAL_TOOL in agent.bound_names[0]
    assert EXTERNAL_TOOL not in agent.bound_names[1]


def test_an_ordinary_file_does_not_shut_the_door(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "gold.txt").write_text("sell at 4600", encoding="utf-8")
    cfg.files.roots = [str(root)]

    replies = [
        Reply(tool_calls=[ToolCall("read_file", {"path": str(root / "gold.txt")})]),
        Reply(content="done"),
    ]
    runtime, agent = _runtime(cfg, replies)
    state = runtime.run_turn("what is my sell target")

    assert state["tainted"] is False
    assert state["saw_private"] is True
    assert EXTERNAL_TOOL in agent.bound_names[1]


def test_a_key_in_a_file_is_redacted_before_the_agent_sees_it(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "mail.txt").write_text(
        "Hi, the deploy token is ghp_16C7e42F292c6912E7710c838347Ae178B4a, thanks",
        encoding="utf-8",
    )
    cfg.files.roots = [str(root)]

    replies = [
        Reply(tool_calls=[ToolCall("read_file", {"path": str(root / "mail.txt")})]),
        Reply(content="done"),
    ]
    runtime, _ = _runtime(cfg, replies)
    state = runtime.run_turn("what did that mail say")

    content = state["tool_results"][0].content
    assert "ghp_16C7e42F292c6912E7710c838347Ae178B4a" not in content
    assert guardrail.REDACTED in content
    assert state["redactions"] == 1
    assert guardrail.NOTICE_REDACTED in state["notices"]  # type: ignore[typeddict-item]
    # A redacted key is not a taint: the turn continues, the door stays open.
    assert state["tainted"] is False


def test_the_tool_call_cap_stops_the_loop(cfg, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    cfg.files.roots = [str(root)]
    cfg.limits.tool_calls = 2

    call = ToolCall("read_file", {"path": str(root / "a.txt")})
    replies = [Reply(tool_calls=[call, call, call, call])]
    runtime, _ = _runtime(cfg, replies)
    state = runtime.run_turn("read it over and over")

    assert state["tool_calls"] == 2
    assert "stopped after 2 tool calls" in (state["unresolved"] or "")


def test_flags_default_to_an_open_door(cfg):
    flags = Flags()
    assert (flags.tainted, flags.blocked, flags.redactions) == (False, False, 0)

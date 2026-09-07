"""Milestone 10: the sidecar exposes the protocol, and a plain client can
drive a whole turn over it."""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from sunday.agent.llm import Reply
from sunday.runtime import Runtime
from sunday.server import Handshake, Sidecar


class Talker:
    def chat(self, messages, *, tools=None, think=False, max_tokens=None):
        return Reply(content="")

    def stream(self, messages, *, think=False):
        for piece in ["Gold is at ", "2,412.55. ", "Anything else?"]:
            yield piece

    def preflight(self):
        return None


class NoMemory:
    def retrieve(self, query, **kwargs):
        return []

    def add_turn(self, **kwargs):
        return None

    def add_session_summary(self, **kwargs):
        return None

    def count(self):
        return 0


@pytest.fixture
async def sidecar(cfg, tmp_path, monkeypatch):
    from sunday import config as config_module

    monkeypatch.setattr(config_module, "HANDSHAKE_PATH", tmp_path / "handshake.json")
    runtime = Runtime(cfg, agent=Talker(), memory=NoMemory())  # type: ignore[arg-type]
    side = Sidecar(runtime, port=0, token="test-token")

    task = asyncio.create_task(side.serve())
    for _ in range(200):
        if (tmp_path / "handshake.json").exists():
            break
        await asyncio.sleep(0.01)
    yield side, json.loads((tmp_path / "handshake.json").read_text(encoding="utf-8"))
    await side.stop()
    await asyncio.wait_for(task, timeout=5)


async def _connect(handshake, token="test-token"):
    ws = await websockets.connect(f"ws://127.0.0.1:{handshake['port']}")
    await ws.send(json.dumps({"type": "hello", "token": token}))
    return ws


async def _drain(ws, until="done", limit=200):
    out = []
    for _ in range(limit):
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        out.append(message)
        if message.get("type") == until:
            break
    return out


@pytest.mark.asyncio
async def test_the_handshake_names_a_loopback_port_and_a_token(sidecar):
    _, handshake = sidecar
    assert handshake["port"] > 0
    assert handshake["token"] == "test-token"
    assert handshake["protocol"] == 1
    assert handshake["pid"] > 0


@pytest.mark.asyncio
async def test_a_wrong_token_is_refused(sidecar):
    _, handshake = sidecar
    ws = await websockets.connect(f"ws://127.0.0.1:{handshake['port']}")
    await ws.send(json.dumps({"type": "hello", "token": "wrong"}))
    message = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert message == {"type": "error", "text": "bad token"}
    with pytest.raises(websockets.ConnectionClosed):
        await asyncio.wait_for(ws.recv(), timeout=5)


@pytest.mark.asyncio
async def test_a_client_can_drive_a_whole_turn(sidecar):
    _, handshake = sidecar
    ws = await _connect(handshake)

    ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert ready["type"] == "ready"
    assert json.loads(await ws.recv())["value"] == "idle"

    await ws.send(json.dumps({"type": "text_input", "text": "how much is gold"}))
    messages = await _drain(ws)

    kinds = [m["type"] for m in messages]
    assert "state" in kinds and "token" in kinds and "sentence" in kinds
    assert kinds[-1] == "done"

    reply = "".join(m["text"] for m in messages if m["type"] == "token")
    assert reply == "Gold is at 2,412.55. Anything else?"
    spoken = [m["text"] for m in messages if m["type"] == "sentence"]
    assert spoken == ["Gold is at 2,412.55.", "Anything else?"]
    assert [m for m in messages if m["type"] == "done"][0]["committed"] is True
    await ws.close()


@pytest.mark.asyncio
async def test_ping_answers_pong(sidecar):
    _, handshake = sidecar
    ws = await _connect(handshake)
    await _drain(ws, until="state")
    await ws.send(json.dumps({"type": "ping"}))
    assert json.loads(await asyncio.wait_for(ws.recv(), timeout=5)) == {"type": "pong"}
    await ws.close()


@pytest.mark.asyncio
async def test_mode_and_mute_are_reported_back(sidecar):
    side, handshake = sidecar
    ws = await _connect(handshake)
    await _drain(ws, until="state")

    await ws.send(json.dumps({"type": "set_mode", "mode": "voice"}))
    assert json.loads(await ws.recv()) == {"type": "mode", "value": "voice"}
    assert side.mode == "voice"

    await ws.send(json.dumps({"type": "set_mute", "muted": True}))
    assert json.loads(await ws.recv()) == {"type": "state", "value": "muted"}
    assert side.muted is True
    await ws.close()


@pytest.mark.asyncio
async def test_an_unknown_message_is_an_error_not_a_crash(sidecar):
    _, handshake = sidecar
    ws = await _connect(handshake)
    await _drain(ws, until="state")
    await ws.send(json.dumps({"type": "nonsense"}))
    message = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert message["type"] == "error"
    await ws.send(json.dumps({"type": "ping"}))
    assert json.loads(await ws.recv())["type"] == "pong"
    await ws.close()


@pytest.mark.asyncio
async def test_the_handshake_is_removed_on_shutdown(cfg, tmp_path, monkeypatch):
    from sunday import config as config_module

    path = tmp_path / "handshake.json"
    monkeypatch.setattr(config_module, "HANDSHAKE_PATH", path)
    runtime = Runtime(cfg, agent=Talker(), memory=NoMemory())  # type: ignore[arg-type]
    side = Sidecar(runtime, port=0, token="t")

    task = asyncio.create_task(side.serve())
    for _ in range(200):
        if path.exists():
            break
        await asyncio.sleep(0.01)
    assert path.exists()

    await side.stop()
    await asyncio.wait_for(task, timeout=5)
    assert not path.exists()


# -- round 2, finding 1: the read loop must stay live during a turn --------


class SlowTalker:
    """A turn long enough to send something into the middle of it."""

    def __init__(self, tool_calls=None):
        self.tool_calls = tool_calls or []

    def chat(self, messages, *, tools=None, think=False, max_tokens=None):
        calls, self.tool_calls = self.tool_calls, []
        return Reply(tool_calls=calls)

    def stream(self, messages, *, think=False):
        import time

        for piece in ["thinking ", "about ", "it ", "now"]:
            time.sleep(0.15)
            yield piece

    def preflight(self):
        return None


@pytest.fixture
async def sidecar_with(cfg, tmp_path, monkeypatch):
    """A sidecar whose agent and roots the test chooses."""
    from sunday import config as config_module

    monkeypatch.setattr(config_module, "HANDSHAKE_PATH", tmp_path / "handshake.json")

    made: list = []

    async def build(agent):
        runtime = Runtime(cfg, agent=agent, memory=NoMemory())  # type: ignore[arg-type]
        side = Sidecar(runtime, port=0, token="test-token")
        task = asyncio.create_task(side.serve())
        made.append((side, task))
        for _ in range(200):
            if (tmp_path / "handshake.json").exists():
                break
            await asyncio.sleep(0.01)
        handshake = json.loads((tmp_path / "handshake.json").read_text(encoding="utf-8"))
        return side, handshake

    yield build

    for side, task in made:
        await side.stop()
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_an_overwrite_can_actually_be_approved_over_the_socket(
    sidecar_with, cfg, tmp_path
):
    """The turn used to be awaited inside the read loop, so the answer sat
    unread in the socket buffer until the timeout refused it."""
    from sunday.agent.llm import ToolCall

    root = tmp_path / "root"
    root.mkdir()
    cfg.files.roots = [str(root)]
    target = root / "gold.txt"
    target.write_text("original", encoding="utf-8")

    agent = SlowTalker(
        [ToolCall("write_file", {"path": str(target), "text": "replaced"})]
    )
    _, handshake = await sidecar_with(agent)
    ws = await _connect(handshake)
    await _drain(ws, until="state")

    await ws.send(json.dumps({"type": "text_input", "text": "rewrite it"}))

    seen = []
    for _ in range(200):
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        seen.append(message)
        if message["type"] == "confirm":
            await ws.send(
                json.dumps(
                    {
                        "type": "confirm_response",
                        "id": message["id"],
                        "approved": True,
                    }
                )
            )
        if message["type"] == "done":
            break

    confirms = [m for m in seen if m["type"] == "confirm"]
    assert len(confirms) == 1  # one prompt, and it carries the id to answer with
    assert confirms[0]["path"] == str(target)
    assert target.read_text(encoding="utf-8") == "replaced"
    await ws.close()


@pytest.mark.asyncio
async def test_cancel_sent_mid_turn_reaches_the_runtime(sidecar_with):
    """`cancel` is documented as the stop button. It went unread until the turn
    it was meant to stop had already committed."""
    _, handshake = await sidecar_with(SlowTalker())
    ws = await _connect(handshake)
    await _drain(ws, until="state")

    await ws.send(json.dumps({"type": "text_input", "text": "say something long"}))
    # Wait for the reply to start, then interrupt it.
    while True:
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if message["type"] == "token":
            break
    await ws.send(json.dumps({"type": "cancel"}))

    done = None
    for _ in range(200):
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if message["type"] == "done":
            done = message
            break

    assert done == {"type": "done", "committed": False}
    await ws.close()


@pytest.mark.asyncio
async def test_a_ping_is_answered_while_a_turn_runs(sidecar_with):
    """The general form of the same bug: the connection stays readable."""
    _, handshake = await sidecar_with(SlowTalker())
    ws = await _connect(handshake)
    await _drain(ws, until="state")

    await ws.send(json.dumps({"type": "text_input", "text": "say something"}))
    while True:
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if message["type"] == "token":
            break

    await ws.send(json.dumps({"type": "ping"}))
    order: list[str] = []
    for _ in range(200):
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if message["type"] in {"pong", "done"}:
            order.append(message["type"])
        if message["type"] == "done":
            break

    # The pong has to arrive *before* the turn finishes, or this passes for a
    # blocked read loop that simply caught up afterwards.
    assert order[:1] == ["pong"], order
    await ws.close()


@pytest.mark.asyncio
async def test_the_backstage_trace_crosses_the_socket(sidecar):
    """The terminal client is not the only one that needs to see the steps --
    the debug page and, later, the shell read the same stream."""
    _, handshake = sidecar
    ws = await _connect(handshake)
    await _drain(ws, until="state")

    await ws.send(json.dumps({"type": "text_input", "text": "hello"}))
    messages = await _drain(ws)

    traces = [m for m in messages if m["type"] == "trace"]
    assert traces, [m["type"] for m in messages]
    assert {"step", "heading", "text", "detail"} <= set(traces[0])
    steps = {m["step"] for m in traces}
    assert {"memory_read", "agent", "compose_reply", "done"} <= steps
    # And the client's end-of-turn marker is still the last thing it sees.
    assert messages[-1]["type"] == "done"

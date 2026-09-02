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

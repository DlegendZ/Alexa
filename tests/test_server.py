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
    """A model that is always up and always pulled.

    The last two are the first-run interface, and this double carries them for
    the same reason `FakeEar` carries the ear's: a stand-in that does not track
    the interface it stands in for stops testing the thing it replaced.
    """

    model = "test-model"

    def chat(self, messages, *, tools=None, think=False, max_tokens=None):
        return Reply(content="")

    def stream(self, messages, *, think=False):
        for piece in ["Gold is at ", "2,412.55. ", "Anything else?"]:
            yield piece

    def preflight(self):
        return None

    def reachable(self):
        return True

    def model_present(self):
        return True


class NoMemory:
    def retrieve(self, query, **kwargs):
        return []

    def add_turn(self, **kwargs):
        return None

    def add_session_summary(self, **kwargs):
        return None

    def count(self):
        return 0


class FakeEar:
    """The ear, without a microphone or a quarter of a gigabyte of ONNX.

    The socket has to be tested against something, and it cannot be the real
    one: these tests run on machines with no sound card and no models, and a
    test that silently skips there is a test that never runs anywhere.
    """

    made: list["FakeEar"] = []

    def __init__(self, cfg, *, on_event=None, on_transcript=None, on_barge_in=None):
        self.cfg = cfg
        self.on_event = on_event
        self.on_transcript = on_transcript
        self.on_barge_in = on_barge_in
        self.ready = True
        self.started = False
        self.stopped = False
        self.said: list[str] = []
        self.hushed = 0
        self.follow_ups = 0
        FakeEar.made.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def say(self, sentence):
        self.said.append(sentence)

    def hush(self):
        self.hushed += 1

    def follow_up(self):
        self.follow_ups += 1

    # -- what the real ear does from its own thread ---------------------

    def emit(self, event):
        self.on_event(event)

    def heard(self, text):
        self.on_transcript(text)

    def barge_in(self):
        self.on_barge_in()


@pytest.fixture
async def sidecar(cfg, tmp_path, monkeypatch):
    from sunday import config as config_module

    monkeypatch.setattr(config_module, "HANDSHAKE_PATH", tmp_path / "handshake.json")
    FakeEar.made.clear()
    runtime = Runtime(cfg, agent=Talker(), memory=NoMemory())  # type: ignore[arg-type]
    side = Sidecar(runtime, port=0, token="test-token", ear_factory=FakeEar)

    task = asyncio.create_task(side.serve())
    for _ in range(200):
        if (tmp_path / "handshake.json").exists():
            break
        await asyncio.sleep(0.01)
    yield side, json.loads((tmp_path / "handshake.json").read_text(encoding="utf-8"))
    await side.stop()
    await asyncio.wait_for(task, timeout=5)


#: How long a test waits for the *next* message before deciding the sidecar is
#: stuck. Generous on purpose: it is a silence budget, not a turn budget, so a
#: longer one costs nothing when the test passes and only matters when it was
#: going to fail anyway. Ten seconds was tight enough that a run competing with
#: a live Ollama probe timed out mid-turn, and reported it as `TimeoutError`
#: with nothing to say about which message never came.
SILENCE_S = 30


async def _recv(ws, expecting="a message", seen=None):
    """One message, or an assertion that says what was being waited for."""
    try:
        return json.loads(await asyncio.wait_for(ws.recv(), timeout=SILENCE_S))
    except asyncio.TimeoutError:  # pragma: no cover - only on a starved box
        got = [m.get("type") for m in (seen or [])]
        raise AssertionError(
            f"waited {SILENCE_S}s for {expecting} and nothing arrived. "
            f"Messages so far: {got or 'none'}. The sidecar is stuck, or this "
            f"machine is too busy to finish a turn in that time."
        ) from None


async def _connect(handshake, token="test-token"):
    ws = await websockets.connect(f"ws://127.0.0.1:{handshake['port']}")
    await ws.send(json.dumps({"type": "hello", "token": token}))
    return ws


async def _drain(ws, until="done", limit=200):
    out = []
    for _ in range(limit):
        message = await _recv(ws, f"a {until!r} message", out)
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
    message = await _recv(ws)
    assert message == {"type": "error", "text": "bad token"}
    with pytest.raises(websockets.ConnectionClosed):
        await asyncio.wait_for(ws.recv(), timeout=5)


@pytest.mark.asyncio
async def test_a_client_can_drive_a_whole_turn(sidecar):
    _, handshake = sidecar
    ws = await _connect(handshake)

    ready = await _recv(ws)
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
    assert await _recv(ws) == {"type": "pong"}
    await ws.close()


@pytest.mark.asyncio
async def test_voice_is_one_switch_and_it_is_reported_back(sidecar):
    """It used to be two, and no combination of them was useful.

    Voice mode with the microphone muted was an ear with its stream stopped;
    a live microphone in text mode heard you and answered in silence.
    """
    side, handshake = sidecar
    ws = await _connect(handshake)
    await _drain(ws, until="state")

    await ws.send(json.dumps({"type": "set_voice", "on": True}))
    assert json.loads(await ws.recv()) == {"type": "voice", "value": True}
    assert side.voice is True

    await ws.send(json.dumps({"type": "set_voice", "on": False}))
    assert json.loads(await ws.recv()) == {"type": "voice", "value": False}
    # Voice off closes the microphone, and `idle` is what says so.
    assert json.loads(await ws.recv()) == {"type": "state", "value": "idle"}
    assert side.voice is False
    await ws.close()


@pytest.mark.asyncio
async def test_an_unknown_message_is_an_error_not_a_crash(sidecar):
    _, handshake = sidecar
    ws = await _connect(handshake)
    await _drain(ws, until="state")
    await ws.send(json.dumps({"type": "nonsense"}))
    message = await _recv(ws)
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


class SlowTalker(Talker):
    """A turn long enough to send something into the middle of it.

    Subclassing rather than copying: the two doubles differ in how long they
    take, not in what they are, and the first-run interface written twice is
    the first-run interface that gets updated once.
    """

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
        message = await _recv(ws)
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
        message = await _recv(ws)
        if message["type"] == "token":
            break
    await ws.send(json.dumps({"type": "cancel"}))

    done = None
    for _ in range(200):
        message = await _recv(ws)
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
        message = await _recv(ws)
        if message["type"] == "token":
            break

    await ws.send(json.dumps({"type": "ping"}))
    order: list[str] = []
    for _ in range(200):
        message = await _recv(ws)
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


# -- voice in (milestone 7) -----------------------------------------------


@pytest.mark.asyncio
async def test_voice_on_opens_the_ear_and_voice_off_closes_it(sidecar):
    side, handshake = sidecar
    ws = await _connect(handshake)
    await _drain(ws, until="state")

    await ws.send(json.dumps({"type": "set_voice", "on": True}))
    assert await _recv(ws) == {"type": "voice", "value": True}
    assert len(FakeEar.made) == 1
    assert FakeEar.made[0].started is True

    await ws.send(json.dumps({"type": "set_voice", "on": False}))
    assert await _recv(ws) == {"type": "voice", "value": False}
    assert FakeEar.made[0].stopped is True
    assert side._ear is None
    await ws.close()


@pytest.mark.asyncio
async def test_a_transcript_starts_a_turn_as_if_it_had_been_typed(sidecar):
    """The whole seam of Stage 01: speech never enters the graph, a string
    does, and the modality is the only thing that knows the difference."""
    _, handshake = sidecar
    ws = await _connect(handshake)
    await _drain(ws, until="state")

    await ws.send(json.dumps({"type": "set_voice", "on": True}))
    await _recv(ws)
    ear = FakeEar.made[0]

    ear.heard("what is the gold price")
    messages = await _drain(ws)

    kinds = [m["type"] for m in messages]
    assert "token" in kinds and kinds[-1] == "done"
    partials = [m for m in messages if m["type"] == "partial"]
    assert partials and partials[0]["text"] == "what is the gold price"


@pytest.mark.asyncio
async def test_the_ear_can_talk_to_the_socket_from_its_own_thread(sidecar):
    """`level` is the one line in the protocol that had no producer. It has
    one now, and it arrives from a thread that is not the loop's."""
    _, handshake = sidecar
    ws = await _connect(handshake)
    await _drain(ws, until="state")

    await ws.send(json.dumps({"type": "set_voice", "on": True}))
    await _recv(ws)
    ear = FakeEar.made[0]

    await asyncio.to_thread(ear.emit, {"type": "level", "rms": 0.42})
    assert await _recv(ws) == {"type": "level", "rms": 0.42}
    await ws.close()


@pytest.mark.asyncio
async def test_a_turn_with_the_ear_open_ends_listening_and_not_idle(sidecar):
    """The state after a reply is the microphone, not the clock.

    It used to broadcast `idle` the moment the model stopped writing, which is
    while the speaker still has sentences queued -- so the orb dropped out of
    `speaking` before the reply had been heard. The ear says `listening` when
    the room is actually quiet again, so the socket says nothing.
    """
    _, handshake = sidecar
    ws = await _connect(handshake)
    await _drain(ws, until="state")

    await ws.send(json.dumps({"type": "set_voice", "on": True}))
    await _recv(ws)

    await ws.send(json.dumps({"type": "text_input", "text": "say something"}))
    await _drain(ws)
    # `done` reaches the client from inside the turn's own loop, and the
    # follow-up is what that loop does *after* it -- so a bare assertion here
    # races the two and fails about one run in twenty. Wait for it rather than
    # for a round trip that only looks like a synchronisation point.
    for _ in range(200):
        if FakeEar.made[0].follow_ups:
            break
        await asyncio.sleep(0.01)
    # One follow-up per turn, and no `idle` racing it.
    assert FakeEar.made[0].follow_ups == 1
    await ws.send(json.dumps({"type": "ping"}))
    assert await _recv(ws) == {"type": "pong"}
    await ws.close()


# -- voice out and barge-in (milestones 8 and 9) --------------------------


@pytest.mark.asyncio
async def test_sentences_reach_the_speaker_as_well_as_the_clients(sidecar):
    """The splitter's second sink now has two readers, and they must not
    diverge -- what is shown and what is spoken are the same text."""
    _, handshake = sidecar
    ws = await _connect(handshake)
    await _drain(ws, until="state")

    await ws.send(json.dumps({"type": "set_voice", "on": True}))
    await _recv(ws)
    ear = FakeEar.made[0]

    await ws.send(json.dumps({"type": "text_input", "text": "say something"}))
    messages = await _drain(ws)

    spoken = [m["text"] for m in messages if m["type"] == "sentence"]
    assert spoken, [m["type"] for m in messages]
    assert ear.said == spoken


@pytest.mark.asyncio
async def test_talking_over_it_stops_it_and_cancels_the_turn(sidecar):
    """Barge-in. Playback and the queue are the ear's problem; the turn is
    the sidecar's, and the person gets told which of the two happened."""
    side, handshake = sidecar
    ws = await _connect(handshake)
    await _drain(ws, until="state")

    await ws.send(json.dumps({"type": "set_voice", "on": True}))
    await _recv(ws)
    ear = FakeEar.made[0]

    cancelled = []
    side.runtime.cancel = lambda: cancelled.append(True)  # type: ignore[method-assign]

    await asyncio.to_thread(ear.barge_in)
    for _ in range(20):
        message = await _recv(ws)
        if message["type"] == "notice":
            assert "you were talking" in message["text"]
            break
    else:  # pragma: no cover - only if the notice never arrives
        raise AssertionError("no notice after barge-in")
    assert cancelled == [True]


@pytest.mark.asyncio
async def test_the_stop_button_also_stops_the_voice(sidecar):
    """`cancel` is the same event arriving from the other side of the room."""
    _, handshake = sidecar
    ws = await _connect(handshake)
    await _drain(ws, until="state")

    await ws.send(json.dumps({"type": "set_voice", "on": True}))
    await _recv(ws)

    await ws.send(json.dumps({"type": "cancel"}))
    await ws.send(json.dumps({"type": "ping"}))
    assert await _recv(ws) == {"type": "pong"}
    assert FakeEar.made[0].hushed == 1
    await ws.close()

# -- the first run ---------------------------------------------------------


class Absent(Talker):
    """A model that is not there, in whichever of the two ways is asked for.

    Two ways, not one, because they are two different jobs for whoever reads
    the answer -- "start Ollama" and "pull a few gigabytes" -- and `preflight`
    collapses them into a single exception, which is right for a startup check
    and wrong for a screen somebody has to act on.
    """

    def __init__(self, *, up=True, pulled=False):
        self.up = up
        self.pulled = pulled

    def reachable(self):
        return self.up

    def model_present(self):
        return self.pulled


@pytest.mark.asyncio
async def test_ready_says_what_a_first_run_is_missing(cfg, tmp_path, monkeypatch):
    """The window has to know before it lets anybody type. A socket that
    accepts a question it cannot answer is worse than one that says what is
    short."""
    from sunday import config as config_module

    monkeypatch.setattr(config_module, "HANDSHAKE_PATH", tmp_path / "handshake.json")
    runtime = Runtime(cfg, agent=Absent(up=True, pulled=False), memory=NoMemory())  # type: ignore[arg-type]
    side = Sidecar(runtime, port=0, token="t")
    task = asyncio.create_task(side.serve())
    for _ in range(200):
        if (tmp_path / "handshake.json").exists():
            break
        await asyncio.sleep(0.01)

    handshake = json.loads((tmp_path / "handshake.json").read_text(encoding="utf-8"))
    async with websockets.connect(f"ws://127.0.0.1:{handshake['port']}") as socket:
        await socket.send(json.dumps({"type": "hello", "token": "t"}))
        ready = json.loads(await socket.recv())
        assert ready["type"] == "ready"
        assert ready["setup"]["ollama"] is True
        # The name comes off the agent rather than the config, so what the
        # screen tells you to pull is the model that will actually be asked
        # for -- one source, not two that can disagree.
        assert ready["setup"]["model"] == runtime.agent.model

    await side.stop()
    await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_a_turn_before_setup_is_finished_says_which_half(cfg, tmp_path, monkeypatch):
    """Not a silent refusal and not a stack trace. Ollama being down and the
    model being unpulled are two different sentences, because they are two
    different things to go and do."""
    from sunday import config as config_module

    monkeypatch.setattr(config_module, "HANDSHAKE_PATH", tmp_path / "handshake.json")
    runtime = Runtime(cfg, agent=Absent(up=False), memory=NoMemory())  # type: ignore[arg-type]
    side = Sidecar(runtime, port=0, token="t")
    task = asyncio.create_task(side.serve())
    for _ in range(200):
        if (tmp_path / "handshake.json").exists():
            break
        await asyncio.sleep(0.01)

    handshake = json.loads((tmp_path / "handshake.json").read_text(encoding="utf-8"))
    async with websockets.connect(f"ws://127.0.0.1:{handshake['port']}") as socket:
        await socket.send(json.dumps({"type": "hello", "token": "t"}))
        await socket.recv()  # ready
        await socket.recv()  # state
        await socket.send(json.dumps({"type": "text_input", "text": "hello"}))

        seen = []
        for _ in range(6):
            message = json.loads(await asyncio.wait_for(socket.recv(), timeout=5))
            seen.append(message)
            if message["type"] == "done":
                break

    kinds = [m["type"] for m in seen]
    assert "error" in kinds
    assert "done" in kinds
    error = next(m for m in seen if m["type"] == "error")
    assert "Ollama" in error["text"]
    # And nothing was remembered, because nothing happened.
    assert next(m for m in seen if m["type"] == "done")["committed"] is False

    await side.stop()
    await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_a_second_fetch_does_not_start_a_second_download(cfg, tmp_path, monkeypatch):
    """One network, one disk, and one `.part` file per model. Two downloads of
    the same file would race for it, which is the failure the partial-write
    rename exists to prevent -- and racing it from two clicks would defeat
    that."""
    from sunday import config as config_module

    monkeypatch.setattr(config_module, "HANDSHAKE_PATH", tmp_path / "handshake.json")
    runtime = Runtime(cfg, agent=Talker(), memory=NoMemory())  # type: ignore[arg-type]
    side = Sidecar(runtime, port=0, token="t")

    started = []
    side._fetch = lambda: started.append(1)  # type: ignore[method-assign]
    side._fetching = True
    side._start_fetch()
    assert started == []

    side._fetching = False
    side._start_fetch()
    await asyncio.sleep(0.05)
    assert started == [1]

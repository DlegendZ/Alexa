r"""The sidecar's socket.

Binds to 127.0.0.1 on an ephemeral port, writes `{port, token}` to
SUNDAY_HOME/handshake.json, and waits. Loopback only, plus a per-launch bearer
token, means nothing else on the machine can drive your assistant.

A socket rather than request/response because what crosses it is a continuous
token stream and frequent state changes. It is also language-agnostic, and you
can point a browser at it to debug -- see web/debug.html.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import websockets
from websockets.asyncio.server import ServerConnection, serve

from sunday import config
from sunday.agent.llm import OllamaDown
from sunday.runtime import ConfirmRequest, Runtime

PROTOCOL_VERSION = 1

#: How long a confirmation waits for an answer before deciding for itself. The
#: decision it makes is "no", because silence is not consent.
CONFIRM_TIMEOUT_S = 120


@dataclass
class _Pending:
    """One question in flight, waited on by the turn's worker thread."""

    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False


@dataclass
class Handshake:
    port: int
    token: str
    pid: int = field(default_factory=os.getpid)
    started: float = field(default_factory=time.time)

    def write(self, path=None) -> None:
        path = path or config.HANDSHAKE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "port": self.port,
                    "token": self.token,
                    "pid": self.pid,
                    "started": self.started,
                    "protocol": PROTOCOL_VERSION,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def clear(path=None) -> None:
        path = path or config.HANDSHAKE_PATH
        try:
            path.unlink()
        except OSError:
            pass


class Sidecar:
    """One runtime, one turn at a time, however many clients are watching."""

    def __init__(
        self,
        runtime: Runtime | None = None,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        token: str | None = None,
        ear_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.runtime = runtime or Runtime()
        #: How the ear gets built. Injectable so the socket can be tested
        #: without a microphone, a quarter of a gigabyte of ONNX, or a machine
        #: that has either.
        self._ear_factory = ear_factory
        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(24)
        self.mode = "text"
        self.muted = False
        self._turn_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._clients: set[ServerConnection] = set()
        self._pending: dict[str, _Pending] = {}
        self._turns: set[asyncio.Task] = set()
        self._relays: set[asyncio.Task] = set()
        #: Built the first time voice mode is asked for, and kept afterwards.
        #: Its models are a quarter of a gigabyte, so it is neither made at
        #: startup -- a text-only session should not pay for them -- nor thrown
        #: away when the mode goes back to text.
        self._ear: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- lifecycle ------------------------------------------------------

    async def serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        async with serve(self._handle, self.host, self.port) as server:
            bound = server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
            Handshake(port=bound, token=self.token).write()
            print(f"sidecar listening on ws://{self.host}:{bound}")
            print(f"handshake written to {config.HANDSHAKE_PATH}")
            try:
                await self._stop.wait()
            finally:
                Handshake.clear()
                self._close_ear()
                self.runtime.shutdown()

    async def stop(self) -> None:
        self._stop.set()

    # -- one client -----------------------------------------------------

    async def _handle(self, websocket: ServerConnection) -> None:
        if not await self._authenticate(websocket):
            return

        self._clients.add(websocket)
        await self._send(
            websocket,
            {"type": "ready", "protocol": PROTOCOL_VERSION, "mode": self.mode,
             "muted": self.muted, "model": self.runtime.cfg.models.agent},
        )
        await self._send(websocket, {"type": "state", "value": "idle"})
        try:
            async for raw in websocket:
                await self._on_message(websocket, raw)
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)

    async def _authenticate(self, websocket: ServerConnection) -> bool:
        """First message must present the token. A browser cannot set headers
        on a WebSocket, so the token travels in the body rather than a URL --
        where it would end up in logs and history."""
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=5)
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            await websocket.close(code=4401, reason="no handshake")
            return False

        message = _parse(raw)
        if message.get("type") != "hello" or not secrets.compare_digest(
            str(message.get("token", "")), self.token
        ):
            await self._send(websocket, {"type": "error", "text": "bad token"})
            await websocket.close(code=4401, reason="bad token")
            return False
        return True

    async def _on_message(self, websocket: ServerConnection, raw: Any) -> None:
        message = _parse(raw)
        kind = message.get("type")

        if kind == "ping":
            await self._send(websocket, {"type": "pong"})
        elif kind == "text_input":
            text = str(message.get("text") or "").strip()
            if text:
                self._start_turn(text, modality="text")
        elif kind == "voice_input":
            text = str(message.get("text") or "").strip()
            if text:
                self._start_turn(text, modality="voice")
        elif kind == "set_mode":
            self.mode = "voice" if message.get("mode") == "voice" else "text"
            await self._broadcast({"type": "mode", "value": self.mode})
            self._set_listening(self.mode == "voice")
        elif kind == "listen":
            # Push to talk. The wake word is the usual way in; this is the mic
            # button and, later, the global hotkey.
            if self._ear is None:
                self._set_listening(True)
            elif self._ear.ready:
                self._ear.trigger()
        elif kind == "set_mute":
            self.muted = bool(message.get("muted"))
            if self._ear is not None:
                self._ear.set_muted(self.muted)
            await self._broadcast(
                {"type": "state", "value": "muted" if self.muted else "idle"}
            )
        elif kind == "confirm_response":
            pending = self._pending.get(str(message.get("id", "")))
            if pending is not None:
                pending.approved = bool(message.get("approved"))
                pending.event.set()
        elif kind == "cancel":
            # A cancelled turn must not leave its question hanging until the
            # timeout: release every waiter, refusing, so the write is skipped.
            for pending in list(self._pending.values()):
                pending.approved = False
                pending.event.set()
            if self._ear is not None:
                self._ear.hush()
            self.runtime.cancel()
        elif kind == "shutdown":
            await self.stop()
        else:
            await self._send(
                websocket, {"type": "error", "text": f"unknown message {kind!r}"}
            )

    # -- the ear --------------------------------------------------------

    def _set_listening(self, on: bool) -> None:
        """Voice mode opens the microphone; text mode closes it again.

        The models load on the ear's own thread, so this returns immediately
        and the socket stays answerable while a quarter of a gigabyte of ONNX
        comes up. What the client sees in the meantime is the state events the
        ear sends for itself.
        """
        if not on:
            self._close_ear()
            return
        if self._ear is not None:
            self._ear.set_muted(self.muted)
            return

        factory = self._ear_factory
        if factory is None:
            from sunday.audio.voice import Ear

            factory = Ear

        self._ear = factory(
            self.runtime.cfg,
            on_event=self._from_ear,
            on_transcript=self._heard,
            on_barge_in=self._barged_in,
        )
        self._ear.set_muted(self.muted)
        self._ear.start()

    def _close_ear(self) -> None:
        ear, self._ear = self._ear, None
        if ear is not None:
            ear.stop()

    def _from_ear(self, event: dict) -> None:
        """Called on the ear's thread. Hand it to the loop and get out."""
        loop = self._loop
        if loop is None:
            return

        def relay() -> None:
            task = asyncio.create_task(self._broadcast(event))
            self._relays.add(task)
            task.add_done_callback(self._relays.discard)

        try:
            loop.call_soon_threadsafe(relay)
        except RuntimeError:
            # The sidecar shut down while the ear was mid-frame. Nothing to
            # tell, and nobody left to tell it to.
            pass

    def _barged_in(self) -> None:
        """Someone talked over it. Playback and the TTS queue are already
        dealt with inside the ear; what is left is the turn itself.

        `Runtime.cancel` is safe to call when nothing is running -- it sets a
        flag the next turn clears -- so this does not have to know whether the
        reply it interrupted had finished.
        """
        self.runtime.cancel()
        self._from_ear({"type": "notice", "text": "stopped, you were talking"})

    def _heard(self, text: str) -> None:
        """One transcript. Goes in exactly as if it had been typed."""
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(
                lambda: self._start_turn(text, modality="voice")
            )
        except RuntimeError:
            pass

    # -- turns ----------------------------------------------------------

    def _start_turn(self, text: str, *, modality: str) -> None:
        """Run the turn beside the read loop, not inside it.

        Awaiting the turn here would block this connection's `async for` until
        it finished, so nothing the client sent mid-turn could be read -- and
        with one client, that is every mid-turn message. `cancel` went unread
        until the turn it was cancelling had already committed, and a `confirm`
        answer sat in the socket buffer while the worker thread waited out the
        two-minute timeout and then refused it.
        """
        task = asyncio.create_task(self._run_turn(text, modality=modality))
        self._turns.add(task)
        task.add_done_callback(self._turns.discard)

    async def _run_turn(self, text: str, *, modality: str) -> None:
        if self._turn_lock.locked():
            await self._broadcast(
                {"type": "notice", "text": "still working on the previous turn"}
            )
            return

        async with self._turn_lock:
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[dict | None] = asyncio.Queue()

            def push(event: dict) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, event)

            def confirm(request: ConfirmRequest) -> bool:
                """Runs on the worker thread. Asks every attached client and
                blocks until one answers or the timeout decides no.

                This is the only place a `confirm` message is produced, so the
                one the client renders is always the one carrying the id it has
                to answer with.
                """
                request_id = secrets.token_urlsafe(8)
                pending = _Pending()
                self._pending[request_id] = pending
                push({
                    "type": "confirm",
                    "id": request_id,
                    "text": request.question,
                    "path": request.path,
                    "action": request.action,
                })
                try:
                    if not pending.event.wait(CONFIRM_TIMEOUT_S):
                        return False
                    return pending.approved
                finally:
                    self._pending.pop(request_id, None)

            def work() -> None:
                try:
                    self.runtime.run_turn(
                        text,
                        modality=modality,
                        on_token=lambda t: push({"type": "token", "text": t}),
                        on_sentence=self._speak,
                        on_event=push,
                        on_confirm=confirm,
                    )
                except OllamaDown as exc:
                    push({"type": "error", "text": str(exc)})
                    push({"type": "done", "committed": False})
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, None)

            await self._broadcast({"type": "partial", "text": text, "final": True})
            task = asyncio.create_task(asyncio.to_thread(work))
            while True:
                event = await queue.get()
                if event is None:
                    break
                await self._broadcast(event)
            await task
            await self._broadcast({"type": "state", "value": "idle"})

    def _speak(self, sentence: str) -> None:
        """The splitter's second sink. It goes to the clients either way; when
        there is an ear attached it also goes to the speaker, one sentence at a
        time, while the model is still writing the next one."""
        ear = self._ear
        if ear is not None:
            ear.say(sentence)
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(
                lambda: self._relays.add(
                    asyncio.create_task(
                        self._broadcast({"type": "sentence", "text": sentence})
                    )
                )
            )
        except RuntimeError:
            pass

    # -- sending --------------------------------------------------------

    async def _send(self, websocket: ServerConnection, message: dict) -> None:
        try:
            await websocket.send(json.dumps(message, ensure_ascii=False))
        except websockets.ConnectionClosed:
            self._clients.discard(websocket)

    async def _broadcast(self, message: dict) -> None:
        for websocket in list(self._clients):
            await self._send(websocket, message)


def _parse(raw: Any) -> dict:
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}

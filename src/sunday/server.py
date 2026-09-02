"""The sidecar's socket.

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
import time
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection, serve

from sunday import config
from sunday.agent.llm import OllamaDown
from sunday.runtime import Runtime

PROTOCOL_VERSION = 1


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
    ) -> None:
        self.runtime = runtime or Runtime()
        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(24)
        self.mode = "text"
        self.muted = False
        self._turn_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._clients: set[ServerConnection] = set()

    # -- lifecycle ------------------------------------------------------

    async def serve(self) -> None:
        async with serve(self._handle, self.host, self.port) as server:
            bound = server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
            Handshake(port=bound, token=self.token).write()
            print(f"sidecar listening on ws://{self.host}:{bound}")
            print(f"handshake written to {config.HANDSHAKE_PATH}")
            try:
                await self._stop.wait()
            finally:
                Handshake.clear()
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
                await self._run_turn(text, modality="text")
        elif kind == "voice_input":
            text = str(message.get("text") or "").strip()
            if text:
                await self._run_turn(text, modality="voice")
        elif kind == "set_mode":
            self.mode = "voice" if message.get("mode") == "voice" else "text"
            await self._broadcast({"type": "mode", "value": self.mode})
        elif kind == "set_mute":
            # Muting stops the capture stream once there is one to stop; until
            # then it is recorded and reported, nothing more.
            self.muted = bool(message.get("muted"))
            await self._broadcast(
                {"type": "state", "value": "muted" if self.muted else "idle"}
            )
        elif kind == "cancel":
            self.runtime.cancel()
        elif kind == "shutdown":
            await self.stop()
        else:
            await self._send(
                websocket, {"type": "error", "text": f"unknown message {kind!r}"}
            )

    # -- turns ----------------------------------------------------------

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

            def work() -> None:
                try:
                    self.runtime.run_turn(
                        text,
                        modality=modality,
                        on_token=lambda t: push({"type": "token", "text": t}),
                        on_sentence=lambda s: push({"type": "sentence", "text": s}),
                        on_event=push,
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

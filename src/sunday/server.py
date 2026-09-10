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
from sunday import settings as settings_module
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
        #: One switch, not two. "Voice mode" and "the microphone is live" and
        #: "it speaks its replies" were three controls for one fact, and no
        #: combination of them was useful: a live microphone in text mode
        #: heard you and answered in silence, and voice mode muted was an ear
        #: with its stream stopped. On means the ear is open, the wake word is
        #: listening and replies are spoken; off means the window is a text
        #: box. Typing works either way, which is why this is not a mode.
        self.voice = False
        self._turn_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._clients: set[ServerConnection] = set()
        self._pending: dict[str, _Pending] = {}
        self._turns: set[asyncio.Task] = set()
        self._relays: set[asyncio.Task] = set()
        #: Built the first time voice is switched on. Its models are a quarter
        #: of a gigabyte, so a session that never turns voice on never pays
        #: for them.
        self._ear: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        #: True while the first-run download is running. One at a time, for the
        #: same reason one turn is: there is one network and one disk, and two
        #: downloads of the same file would race for the same `.part`.
        self._fetching = False
        #: True while the Google consent flow is waiting on a browser. One at a
        #: time: two would be two loopback servers and two `prompt=consent`
        #: redirects, and whichever finished second would overwrite the token
        #: the first had just earned.
        self._authorising = False

    # -- lifecycle ------------------------------------------------------

    async def serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        async with serve(self._handle, self.host, self.port) as server:
            bound = server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
            Handshake(port=bound, token=self.token).write()
            if self.runtime.cfg.audio.listen_on_start:
                # The ear before the first client. Starting it here rather
                # than on a `set_voice` is what lets the app answer its name
                # from a cold start -- the window may not be open yet, and
                # the whole point of a wake word is that it does not need to
                # be. The models load on the ear's own thread, so this does
                # not delay the socket.
                self.voice = True
                self._set_listening(True)
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
        await self._send(websocket, self._ready())
        # The resting state, not a fixed one. A window that attaches to a
        # sidecar which has been listening since boot must not be told the
        # microphone is shut -- `listen_on_start` means the ear is usually
        # already up by the time anybody opens the window.
        await self._send(websocket, {"type": "state", "value": self._resting()})
        try:
            async for raw in websocket:
                await self._on_message(websocket, raw)
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)

    def _ready(self) -> dict:
        """What the window needs before it can show anything.

        `setup` is the first-run half, and it is on `ready` rather than behind
        a request because the window has to know before it lets anybody type.
        A socket that accepts a question it cannot answer is worse than one
        that says what is missing.
        """
        return {
            "type": "ready",
            "protocol": PROTOCOL_VERSION,
            "voice": self.voice,
            "model": self.runtime.cfg.models.agent,
            # The window puts this above the transcript, so it travels rather
            # than being written down there. `[assistant] name` is the one
            # place it is decided, and a window with "Alexa" in its markup is
            # a window that is wrong the day somebody changes it.
            "name": self.runtime.cfg.assistant.name,
            "setup": self.runtime.setup_needed(),
        }

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
        elif kind == "set_voice":
            self.voice = bool(message.get("on"))
            await self._broadcast({"type": "voice", "value": self.voice})
            self._set_listening(self.voice)
            if not self.voice:
                # An ear that has just been shut has no state to report from
                # its own thread, so the resting state is said here. `idle`
                # means the microphone is closed, and it is the only thing
                # that means it: with the ear open the turn ends in
                # `listening`.
                await self._broadcast({"type": "state", "value": "idle"})
        elif kind == "fetch_models":
            self._start_fetch()
        elif kind == "get_settings":
            await self._send(websocket, self._settings())
        elif kind == "save_settings":
            await self._save_settings(message)
        elif kind == "forget_all":
            await self._forget_all()
        elif kind == "google_auth":
            self._start_google_auth()
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

    # -- settings -------------------------------------------------------

    def _settings(self) -> dict:
        """The whole settings payload, assembled in one place.

        One producer, like `ready`, and for the same reason: it is a snapshot
        of several facts at once, and two places building it is two places to
        forget the field that was added last. Everything in it is safe to send
        -- `settings.credentials()` returns presence and four characters, never
        a key.
        """
        remembered = None
        try:
            remembered = self.runtime.memory.count()
        except Exception:  # noqa: BLE001 - a store that will not open is a number we do not have
            remembered = None
        payload = settings_module.describe(self.runtime.cfg, remembered=remembered)
        # Spelled as a literal on purpose. `tests/test_shell.py` finds what the
        # sidecar can send by reading `"type": "..."` out of this file, so
        # setting the key afterwards makes the message invisible to the test
        # that exists to notice the window forgetting to handle it -- the
        # `trace.query_left` bug, in the machinery built to catch it.
        return {"type": "settings", **payload}

    async def _save_settings(self, message: dict) -> None:
        """Write the settings, then make the running process agree with them.

        Refused while a turn is in flight. A turn reads the config as it goes
        -- the folders when it resolves a path, the caps when it counts hops --
        so swapping it underneath one would give that turn two configurations
        and no way to tell which answered. Waiting is a second; the confusion
        would be permanent and unreproducible.
        """
        if self._turn_lock.locked():
            await self._broadcast({
                "type": "saved",
                "ok": False,
                "error": "still working on a turn -- try again when it finishes",
                "restart": [],
                "warnings": [],
            })
            return

        try:
            # Credentials go through the same call, so they are written only
            # once every value has been checked. They never came down to the
            # window as values, and only the boxes the user typed into come
            # back.
            given = message.get("credentials")
            outcome = settings_module.apply(
                message.get("values") or {},
                credentials=given if isinstance(given, dict) else None,
            )
        except settings_module.Invalid as exc:
            await self._broadcast({
                "type": "saved",
                "ok": False,
                "error": str(exc),
                "restart": [],
                "warnings": [],
            })
            return
        except Exception as exc:  # noqa: BLE001 - a failed save is a message
            await self._broadcast({
                "type": "saved",
                "ok": False,
                "error": f"the settings could not be written: {type(exc).__name__}: {exc}",
                "restart": [],
                "warnings": [],
            })
            return

        self.runtime.reload_config()
        if outcome.audio_changed and self._ear is not None:
            # The ear reads the whole config when it is built and never again,
            # so a microphone setting that saves and does nothing is the shape
            # of failure this screen exists to avoid. Closing and reopening is
            # what the voice switch already does, and it is a second.
            self._close_ear()
            self._set_listening(True)

        await self._broadcast({
            "type": "saved",
            "ok": True,
            "error": "",
            "restart": outcome.restart,
            "warnings": outcome.warnings,
        })
        await self._broadcast(self._settings())

    async def _forget_all(self) -> None:
        """Empty long-term memory. Irreversible, and refused mid-turn.

        Mid-turn because Chroma is SQLite and the turn in flight will write to
        it when it commits -- dropping the collection underneath that is the
        force-kill lesson from the other direction.
        """
        if self._turn_lock.locked():
            await self._broadcast({
                "type": "forgot",
                "ok": False,
                "removed": 0,
                "error": "still working on a turn -- try again when it finishes",
            })
            return
        # Held for the drop, not only checked before it. The drop runs on a
        # thread, and a check followed by an await is a window: a question
        # asked inside it started a turn against a collection being deleted,
        # which is the race the check was written to refuse.
        async with self._turn_lock:
            try:
                removed = await asyncio.to_thread(self.runtime.forget_everything)
            except Exception as exc:  # noqa: BLE001
                await self._broadcast({
                    "type": "forgot",
                    "ok": False,
                    "removed": 0,
                    "error": f"nothing was deleted: {type(exc).__name__}: {exc}",
                })
                return
        await self._broadcast(
            {"type": "forgot", "ok": True, "removed": removed, "error": ""}
        )
        await self._broadcast(self._settings())

    # -- signing in to Google -------------------------------------------

    def _start_google_auth(self) -> None:
        """Run the consent flow beside the read loop.

        Consent cannot happen inside a *turn* -- a model would be waiting on a
        tool result while a browser window may never open. It can happen here:
        nothing is waiting, no confirmation timeout is running, and a browser
        opening is what the user just pressed a button to ask for.

        One at a time, and the flow has its own five-minute bound, so a browser
        tab that is closed rather than answered ends rather than leaking the
        thread.
        """
        if self._authorising:
            return
        self._authorising = True
        task = asyncio.create_task(asyncio.to_thread(self._google_auth))
        self._turns.add(task)
        task.add_done_callback(self._turns.discard)

    def _google_auth(self) -> None:
        from sunday.tools import google

        loop = self._loop

        def say(text: str, status: str = "running") -> None:
            if loop is None:
                return
            try:
                loop.call_soon_threadsafe(
                    lambda: self._relay(
                        self._broadcast(
                            {"type": "google_auth", "status": status, "text": text}
                        )
                    )
                )
            except RuntimeError:
                pass

        try:
            code = google.authorise(say=say)
            if code == 0:
                say("Signed in to Google.", "done")
                if loop is not None:
                    loop.call_soon_threadsafe(
                        lambda: self._relay(self._broadcast(self._settings()))
                    )
            else:
                say("Sign-in did not finish. Nothing was saved.", "error")
        except Exception as exc:  # noqa: BLE001 - a failed sign-in is a message
            say(f"sign-in stopped: {type(exc).__name__}: {exc}", "error")
        finally:
            self._authorising = False

    # -- the first run --------------------------------------------------

    def _start_fetch(self) -> None:
        """Download whatever is missing, saying so as it goes.

        A few gigabytes on a first launch, so it reports bytes rather than
        spinning: a progress screen that cannot say how far along it is reads
        as a hang, and the honest answer is available the whole time.

        It runs here rather than in the shell because the numbers, the URLs and
        the rule about which transcriber is wanted all live on this side
        already. A second downloader would be a second thing to keep in step.
        """
        if self._fetching:
            return
        self._fetching = True
        task = asyncio.create_task(asyncio.to_thread(self._fetch))
        self._turns.add(task)
        task.add_done_callback(self._turns.discard)

    def _fetch(self) -> None:
        from sunday.audio import models

        loop = self._loop

        def say(event: dict) -> None:
            if loop is None:
                return
            try:
                loop.call_soon_threadsafe(
                    lambda: self._relay(self._broadcast(event))
                )
            except RuntimeError:
                pass

        try:
            need = self.runtime.setup_needed()
            if not need["ollama"]:
                say({
                    "type": "fetch",
                    "status": "error",
                    "text": (
                        "Ollama is not running. Start it and try again -- "
                        "nothing here can install it for you."
                    ),
                })
                return

            if need["model"]:
                say({"type": "fetch", "what": need["model"], "status": "starting"})
                self.runtime.agent.pull(
                    on_progress=lambda status, done, total: say({
                        "type": "fetch",
                        "what": need["model"],
                        "status": status,
                        "done": done,
                        "total": total,
                    })
                )

            for key in need["models"]:
                say({"type": "fetch", "what": key, "status": "starting"})
            models.fetch(
                on_progress=lambda key, done, total: say({
                    "type": "fetch",
                    "what": key,
                    "status": "downloading",
                    "done": done,
                    "total": total,
                })
            )
            say({"type": "fetch", "status": "done"})
            say(self._ready())
        except Exception as exc:  # noqa: BLE001 - a failed download is a message
            say({
                "type": "fetch",
                "status": "error",
                "text": f"the download stopped: {type(exc).__name__}: {exc}",
            })
        finally:
            self._fetching = False

    def _relay(self, coroutine: Any) -> None:
        """Keep a reference to a broadcast started from another thread, so it
        is not garbage collected mid-send."""
        task = asyncio.create_task(coroutine)
        self._relays.add(task)
        task.add_done_callback(self._relays.discard)

    # -- the ear --------------------------------------------------------

    def _resting(self) -> str:
        """What the orb shows between turns.

        Two states, and the difference between them is the microphone: with
        the ear open the app is genuinely listening, so it says so and the orb
        rides the level. `idle` is the closed microphone, and it is the only
        thing that means that now the mute switch is gone.
        """
        ear = self._ear
        return "listening" if ear is not None and ear.ready else "idle"

    def _set_listening(self, on: bool) -> None:
        """Voice on opens the microphone; voice off closes it again.

        The models load on the ear's own thread, so this returns immediately
        and the socket stays answerable while a quarter of a gigabyte of ONNX
        comes up. What the client sees in the meantime is the state events the
        ear sends for itself.
        """
        if not on:
            self._close_ear()
            return
        if self._ear is not None:
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

        if not self.runtime.ready_to_answer():
            # A first run that has not finished. Saying which half is missing
            # is the difference between a screen somebody can act on and one
            # that reads as the app being broken.
            need = self.runtime.setup_needed()
            await self._broadcast({
                "type": "error",
                "text": (
                    "Ollama is not running -- start it, then try again."
                    if not need["ollama"]
                    else f"the model {need['model']} has not been pulled yet"
                ),
            })
            await self._broadcast({"type": "done", "committed": False})
            return

        async with self._turn_lock:
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[dict | None] = asyncio.Queue()

            def push(event: dict) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, event)

            def speak(sentence: str) -> None:
                """The splitter's second sink, which now has two readers.

                The speaker is told straight away, on this thread, because a
                trip through the loop is latency you can hear. The clients are
                told through the same queue as everything else -- a second
                path would race the first, and `done` travels on the queue.
                Clients stop listening at `done`, so a sentence that overtook
                it is a sentence nobody sees. That is the ordering mistake this
                codebase has now made three times.
                """
                ear = self._ear
                if ear is not None:
                    ear.say(sentence)
                push({"type": "sentence", "text": sentence})

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
                        on_sentence=speak,
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
            # The door stays open for a moment, so the next question needs no
            # wake word. The ear waits for the reply to finish being spoken
            # before it starts counting, and *it* says `listening` when the
            # room is quiet again -- which is the whole reason the state is
            # not broadcast from here as well. Saying `idle` at the end of the
            # turn was saying it while Kokoro still had four sentences queued,
            # so the orb dropped out of `speaking` the moment the model
            # stopped writing rather than when the reply stopped being heard.
            ear = self._ear
            if ear is not None and ear.ready:
                ear.follow_up()
            else:
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

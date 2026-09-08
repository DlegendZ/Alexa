"""The ear: microphone to listener to Moonshine to one string.

This is where the four pieces are actually wired together, and it is the last
file in the package that knows anything about audio. What leaves here is text,
and text goes into the graph exactly as if it had been typed.

It owns one thread. The models load on it rather than in the constructor, so
starting the ear never blocks the socket -- a quarter of a gigabyte of ONNX
takes a second or two to come up, and the sidecar has to stay answerable while
it does. Until they are loaded, `ready` is false and the state says so.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from sunday import config
from sunday.audio import models
from sunday.audio.capture import Microphone, NoAudio
from sunday.audio.listener import Clip, Listener

EventSink = Callable[[dict[str, Any]], None]
TranscriptSink = Callable[[str], None]


class Ear:
    """Everything from the microphone up to, but not including, the graph."""

    def __init__(
        self,
        cfg: config.Config | None = None,
        *,
        on_event: EventSink | None = None,
        on_transcript: TranscriptSink | None = None,
    ) -> None:
        self.cfg = cfg or config.get()
        self._on_event = on_event
        self._on_transcript = on_transcript

        self.ready = False
        self.error: str | None = None

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._mic: Microphone | None = None
        self._listener: Listener | None = None
        self._stt: Any = None
        #: What Kokoro is saying right now, for the transcript check. Set by
        #: the speaker in milestone 8; read here and nowhere else.
        self.spoken_now = ""

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sunday-ear", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._mic is not None:
            self._mic.stop()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=3)
        self.ready = False

    # -- what the app can change while it runs --------------------------

    def set_muted(self, muted: bool) -> None:
        """Muted stops the capture stream, not just the decisions about it."""
        if self._listener is not None:
            self._listener.muted = muted
        if muted:
            if self._mic is not None:
                self._mic.stop()
                self._mic = None
            self._say(state="muted")
        elif self._thread is not None and self._mic is None:
            self._stop.clear()
            self._thread = None
            self.start()

    def set_speaking(self, speaking: bool, *, sentence: str = "") -> None:
        """Raises the VAD bar while Kokoro plays -- see Echo control."""
        self.spoken_now = sentence
        if self._listener is not None:
            self._listener.speaking = speaking

    def trigger(self) -> None:
        """Push to talk: the global hotkey and the mic button both land here."""
        if self._listener is not None and self._listener.phase == "sleeping":
            self._listener.open()
            self._say(state="listening")

    def abandon(self) -> None:
        if self._listener is not None:
            self._listener.abandon()

    # -- the thread -----------------------------------------------------

    def _run(self) -> None:
        try:
            self._load()
        except (models.ModelMissing, NoAudio) as exc:
            self.error = str(exc)
            self._emit({"type": "error", "text": str(exc)})
            self._say(state="error")
            return
        except Exception as exc:  # a broken model file, a missing runtime
            self.error = f"voice could not start: {exc}"
            self._emit({"type": "error", "text": self.error})
            self._say(state="error")
            return

        self.ready = True
        self._say(state="idle")
        assert self._mic is not None and self._listener is not None
        for frame in self._mic.frames():
            if self._stop.is_set():
                break
            for event in self._listener.frame(frame):
                self._handle(event)

        # The frames ran out without anyone asking them to. That is the
        # microphone giving up, not the app shutting down, and it has to look
        # different from a quiet room.
        self.ready = False
        if not self._stop.is_set():
            self._say(state="error")

    def _load(self) -> None:
        from sunday.audio.stt import Moonshine
        from sunday.audio.vad import Vad
        from sunday.audio.wake import WakeWord

        wake = None
        if self.cfg.wake.enabled:
            wake = WakeWord(self.cfg.wake.model, threshold=self.cfg.wake.threshold)
        self._listener = Listener(self.cfg, wake=wake, vad=Vad(sample_rate=self.cfg.audio.sample_rate))
        self._stt = Moonshine()
        self._mic = Microphone(self.cfg, on_error=self._device_trouble)

    # -- events ---------------------------------------------------------

    def _handle(self, event: Any) -> None:
        if event.kind == "level":
            self._emit({"type": "level", "rms": event.rms})
        elif event.kind == "wake":
            self._emit({"type": "state", "value": "listening"})
        elif event.kind == "dropped":
            # Say why. A clip that is dropped in silence is indistinguishable
            # from a wake word that never fired and from a microphone that was
            # never bound -- three different faults, one blank terminal. The
            # listener already knows which; throwing that away here is the
            # same bug as a trace line that is written and never emitted.
            self._emit({"type": "notice", "text": f"heard nothing usable: {event.why}"})
            self._emit({"type": "state", "value": "idle"})
        elif event.kind == "clip" and event.clip is not None:
            self._transcribe(event.clip)

    def _transcribe(self, clip: Clip) -> None:
        self._say(state="transcribing")
        try:
            text = self._stt.transcribe(clip.audio)
        except Exception as exc:
            self._emit({"type": "error", "text": f"could not transcribe that: {exc}"})
            self._say(state="idle")
            return

        if not text:
            # It heard speech, closed a clip, and the transcriber had nothing
            # to say about it. That is a different fault from hearing nothing,
            # and the numbers are the only way to tell them apart.
            self._emit({
                "type": "notice",
                "text": f"transcribed {clip.speech_ms} ms of speech as nothing",
            })
            self._say(state="idle")
            return

        self._emit({"type": "partial", "text": text, "final": True})
        if self._on_transcript is not None:
            self._on_transcript(text)
        else:
            self._say(state="idle")

    def _device_trouble(self, text: str) -> None:
        self._emit({"type": "error", "text": text})

    def _emit(self, event: dict[str, Any]) -> None:
        if self._on_event is not None:
            self._on_event(event)

    def _say(self, *, state: str) -> None:
        self._emit({"type": "state", "value": state})


__all__ = ["Ear"]

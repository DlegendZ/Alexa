"""The ear and the mouth: microphone to text, and sentences back to sound.

This is where the pieces are actually wired together, and it is the last file
in the package that knows anything about audio. What leaves here is text, and
text goes into the graph exactly as if it had been typed.

It owns one thread for listening and hands the speaker its own. The models load
on the listening thread rather than in the constructor, so starting the ear
never blocks the socket -- a third of a gigabyte of ONNX takes a second or two
to come up, and the sidecar has to stay answerable while it does. Until they
are loaded, `ready` is false and the state says so.

The mouth lives here rather than beside it because the three layers of echo
control all cross between them. Layer 1 needs the exact samples that went to
the speaker, layer 2 needs to know when it is playing, and layer 3 needs to
know what it said. Two objects that have to agree about all three at every
moment are one object with a seam drawn through it.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Callable

import numpy as np

from sunday import config
from sunday.audio import echo as echo_check
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
        on_barge_in: Callable[[], None] | None = None,
    ) -> None:
        self.cfg = cfg or config.get()
        self._on_event = on_event
        self._on_transcript = on_transcript
        #: Called when someone talks over Sunday. Playback and the queue are
        #: already dealt with here; what this has to do is cancel the turn.
        self._on_barge_in = on_barge_in

        self.ready = False
        self.error: str | None = None

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._mic: Microphone | None = None
        self._listener: Listener | None = None
        self._stt: Any = None
        self._speaker: Any = None
        self._aec: Any = None
        #: Set when a turn ends while Sunday is still speaking. The follow-up
        #: window starts when the reply does, not when the model stops writing.
        self._pending_follow_up = False
        #: The last few things Kokoro said, for the transcript check. A few
        #: rather than one: transcription finishes after playback has moved on,
        #: so the sentence that came back through the room is usually the one
        #: before the one being spoken now.
        self._spoken: deque[str] = deque(maxlen=3)

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sunday-ear", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._speaker is not None:
            self._speaker.close()
            self._speaker = None
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

    # -- the mouth ------------------------------------------------------

    def say(self, sentence: str) -> None:
        """One sentence, spoken. Queued; this returns straight away."""
        if self._speaker is not None and self.cfg.tts.enabled:
            self._speaker.say(sentence)

    def hush(self) -> None:
        """Stop talking now. Barge-in, cancellation, or a turn that broke."""
        self._pending_follow_up = False
        if self._speaker is not None:
            self._speaker.stop()

    def _on_speaker_state(self, state: str, sentence: str) -> None:
        """Runs on the speaker's thread. Layer 2 of echo control is this line:
        while Kokoro plays, the bar for what counts as a person goes up."""
        if self._listener is not None:
            self._listener.speaking = state == "speaking"
        if state == "speaking" and sentence:
            self._spoken.append(sentence)
            self._emit({"type": "state", "value": "speaking"})
        elif state == "error":
            self._emit({"type": "error", "text": sentence})
        else:
            if self._aec is not None:
                self._aec.silence()
            if self._pending_follow_up and self._listener is not None:
                self._pending_follow_up = False
                self._listener.expect_follow_up()

    def _on_played(self, block: np.ndarray) -> None:
        """Layer 1's reference, and it is bit-exact because we made it."""
        if self._aec is not None:
            self._aec.played(block)

    def trigger(self) -> None:
        """Push to talk: the global hotkey and the mic button both land here."""
        if self._listener is not None and self._listener.phase == "sleeping":
            self._listener.open()
            self._say(state="listening")

    def abandon(self) -> None:
        if self._listener is not None:
            self._listener.abandon()

    def follow_up(self) -> None:
        """The turn is over. Leave the door open for the next question.

        If Sunday is still talking, this waits: the window has to start when
        the reply ends, not when the model stopped writing it, or most of it
        is spent listening to the speaker.
        """
        if self._listener is None:
            return
        if self._speaker is not None and self._speaker.speaking:
            self._pending_follow_up = True
            return
        self._listener.expect_follow_up()

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
            # Layer 1 first, and unconditionally. When nothing is playing there
            # is no reference to align and the frame comes back untouched.
            if self._aec is not None:
                frame = self._aec.process(frame)
                self._listener.reference_rms = self._aec.reference_rms
            for event in self._listener.frame(frame):
                self._handle(event)

        # The frames ran out without anyone asking them to. That is the
        # microphone giving up, not the app shutting down, and it has to look
        # different from a quiet room.
        self.ready = False
        if not self._stop.is_set():
            self._say(state="error")

    def _load(self) -> None:
        from sunday.audio import stt
        from sunday.audio.aec import Aec
        from sunday.audio.speaker import Speaker
        from sunday.audio.vad import Vad
        from sunday.audio.wake import WakeWord

        wake = None
        if self.cfg.wake.enabled:
            wake = WakeWord(self.cfg.wake.model, threshold=self.cfg.wake.threshold)
        self._listener = Listener(
            self.cfg, wake=wake, vad=Vad(sample_rate=self.cfg.audio.sample_rate)
        )
        self._stt = stt.load(self.cfg)
        self._aec = Aec(
            frame=round(self.cfg.audio.sample_rate * 20 / 1000),
            delay_ms=self.cfg.audio.aec_delay_ms,
            rate=self.cfg.audio.sample_rate,
        )
        self._mic = Microphone(self.cfg, on_error=self._device_trouble)
        if self.cfg.tts.enabled:
            self._speaker = Speaker(
                self.cfg, on_played=self._on_played, on_state=self._on_speaker_state
            )
            self._speaker.start()

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
        elif event.kind == "follow_up":
            self._emit({"type": "state", "value": "listening"})
        elif event.kind == "barge_in":
            self._barge_in()
        elif event.kind == "clip" and event.clip is not None:
            self._transcribe(event.clip)

    def _barge_in(self) -> None:
        """Talking over it. Playback stops, the queue is flushed, the turn is
        cancelled, and the clip that is now recording becomes the next one."""
        self.hush()
        self._emit({"type": "state", "value": "listening"})
        if self._on_barge_in is not None:
            self._on_barge_in()

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

        # Layer 3, and only for a clip that could possibly hold an echo.
        #
        # It used to run on every transcript against the last few things said,
        # which reads as reasonable and is not: a reply always contains its
        # question's subject, so asking about that subject again scores as an
        # echo of the answer. Measured on real wording -- "what is the weather
        # in Jakarta" against the reply to it scores 0.67, and the follow-up
        # "and the humidity" scores 1.00, because every word of it is in the
        # answer. Both were being thrown away.
        #
        # The spec says to compare against the sentence Kokoro is *currently
        # speaking*. A clip recorded while nothing was playing cannot contain
        # Sunday's voice, whatever it sounds like.
        if not clip.while_speaking:
            self._deliver(text)
            return

        for sentence in reversed(self._spoken):
            if echo_check.is_echo(
                text, sentence, cutoff=self.cfg.echo.transcript_similarity_cutoff
            ):
                self._emit({
                    "type": "notice",
                    "text": "ignored what sounded like my own voice coming back",
                })
                self._say(state="idle")
                return

        self._deliver(text)

    def _deliver(self, text: str) -> None:
        """Hand the transcript over. From here it is a string like any other."""
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

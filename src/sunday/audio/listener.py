"""The state machine between the microphone and the graph.

Everything with a decision in it lives here, and nothing here touches
hardware or loads a model. It eats 20 ms frames and produces events; the wake
word and the VAD arrive as objects, so a test can hand it two fakes and drive
a whole utterance in a loop with no sound card, no ONNX and no models on disk.

The turn, in audio terms:

1. The wake word fires. The last 0.5 s of the ring buffer is kept, because
   people start talking before they finish saying the trigger.
2. The VAD marks speech. Recording continues while it stays true.
3. 700 ms of silence and you are done. The clip closes.
4. Moonshine transcribes it -- elsewhere; this file hands over a clip.

Two guards on step 3, and one addition. A clip whose *speech* is shorter than
`min_clip_ms` is a cough and is dropped; one longer than `max_clip_ms` is
force-closed and transcribed anyway. And a wake word that fires with nothing
said after it -- the television, mostly -- gives up after `lead_in_ms` rather
than recording silence until the 30 s cap.

Starting and stopping are two different clocks, and conflating them is the
bug this cost an evening to find. `vad_silence_ms` decides when you have
*finished*; it may only run once you have begun. The pre-roll almost always
contains the wake phrase, so counting that as having begun starts the 700 ms
stopwatch during the pause before the question -- which is routinely twice
that long. The clip then closed on the phrase alone and dropped it as a
cough, and from outside it looked exactly like a microphone that had stopped
working.

Time is counted in samples, never from a clock. The same frames always
produce the same events, which is what makes the whole of this testable.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from sunday import config
from sunday.audio import vad as vad_module
from sunday.audio import wake as wake_module

#: The capture frame. 20 ms at 16 kHz is 320 samples.
FRAME_MS = 20

#: How often a level event goes out. Every frame would be fifty a second for a
#: ring that cannot move that fast anyway.
LEVEL_EVERY_MS = 100

#: Kept either side of the speech when the clip is trimmed. Enough that a soft
#: consonant at the very start is not clipped off, and little enough that
#: Moonshine still sees a segment shaped like the ones it was trained on.
TRIM_MARGIN_MS = 250

Phase = Literal["sleeping", "recording"]


@dataclass(frozen=True)
class Clip:
    """One closed utterance, ready for the transcriber."""

    audio: np.ndarray
    speech_ms: int
    total_ms: int
    forced: bool = False
    #: Whether any of this clip was recorded while Sunday was talking. Only
    #: such a clip can hold Sunday's voice, and only such a clip may be thrown
    #: away for sounding like it -- see the transcript check.
    while_speaking: bool = False


@dataclass(frozen=True)
class VoiceEvent:
    """Something the listener wants said out loud to the rest of the app."""

    kind: Literal["wake", "level", "clip", "dropped", "barge_in", "follow_up"]
    score: float = 0.0
    rms: float = 0.0
    clip: Clip | None = None
    why: str = ""


class Listener:
    """Sleeping until the phrase, recording until the silence."""

    def __init__(
        self,
        cfg: config.Config | None = None,
        *,
        wake: Any | None = None,
        vad: Any | None = None,
    ) -> None:
        self.cfg = cfg or config.get()
        audio = self.cfg.audio
        self.rate = audio.sample_rate

        self._wake = wake
        self._vad = vad

        self.phase: Phase = "sleeping"
        #: Raised while Kokoro is playing, so residual echo does not read as a
        #: person. Inert until there is something to play -- see Echo control.
        self.speaking = False
        #: Muted means the capture stream is stopped upstream; this is the
        #: belt to that pair of braces.
        self.muted = False

        self._preroll: deque[np.ndarray] = deque(
            maxlen=max(1, self._ms_to_frames(audio.preroll_ms))
        )
        self._wake_buffer = np.zeros(0, dtype=np.float32)
        self._vad_buffer = np.zeros(0, dtype=np.float32)
        self._clip: list[np.ndarray] = []

        self._clip_samples = 0
        self._speech_samples = 0
        self._silence_samples = 0
        self._cooldown_samples = 0
        self._level_samples = 0
        #: Where the speech sits inside the clip, in samples. The whole clip
        #: goes through the VAD -- pre-roll included -- so these are positions
        #: in the audio that is handed over, not offsets into a later part of
        #: it. `None` means nothing has been heard yet.
        self._windows = 0
        self._first_speech: int | None = None
        self._last_speech: int | None = None
        #: Speech heard since the wake word fired, as against speech sitting in
        #: the pre-roll -- which is almost always the wake phrase itself. The
        #: two have to be counted apart, because the pause between the phrase
        #: and the question is longer than the silence that ends a clip.
        self._live_speech = 0
        self._preroll_samples = 0
        #: Set if any part of the clip was recorded while Sunday was talking.
        #: The transcript check is the last echo layer and the only one that
        #: can throw away a whole sentence, so it may only look at clips that
        #: could possibly hold an echo at all.
        self._overlapped_speech = False
        #: Consecutive speech windows heard while Sunday is talking, or while
        #: the follow-up window is open. Both want a sustained burst rather
        #: than a frame: residual echo is quiet and choppy, and so is a room.
        self._burst = 0
        #: What is left of the window after a reply during which you may just
        #: talk. Counted down in samples like everything else here.
        self._follow_up = 0
        #: How loud what is being played was, this frame, at the microphone's
        #: rate. Set by whoever owns the canceller; zero when nothing is out
        #: there. It is the only thing that can separate Sunday's voice from
        #: yours -- both are speech, and the VAD says 1.0 to either.
        self.reference_rms = 0.0

    # -- units ----------------------------------------------------------

    def _ms_to_frames(self, ms: int) -> int:
        return max(1, round(ms / FRAME_MS))

    def _ms(self, samples: int) -> int:
        return round(samples * 1000 / self.rate)

    def _samples(self, ms: int) -> int:
        return round(ms * self.rate / 1000)

    # -- the stream -----------------------------------------------------

    def frame(self, samples: np.ndarray) -> list[VoiceEvent]:
        """One 20 ms frame of float32 mono. Returns what it decided."""
        block = np.asarray(samples, dtype=np.float32).reshape(-1)
        if self.muted or block.size == 0:
            return []

        events: list[VoiceEvent] = []
        self._emit_level(block, events)

        if self._cooldown_samples > 0:
            self._cooldown_samples = max(0, self._cooldown_samples - block.size)

        if self.phase == "sleeping":
            self._preroll.append(block)
            if self.speaking:
                # Barge-in needs no wake word. You are already talking to it.
                self._open_on_speech(block, events, kind="barge_in")
            elif self._follow_up > 0:
                self._follow_up = max(0, self._follow_up - block.size)
                self._open_on_speech(block, events, kind="follow_up")
            else:
                self._burst = 0
                self._listen_for_wake(block, events)
        else:
            self._record(block, events)
        return events

    def _emit_level(self, block: np.ndarray, events: list[VoiceEvent]) -> None:
        self._level_samples += block.size
        if self._level_samples < self._samples(LEVEL_EVERY_MS):
            return
        self._level_samples = 0
        rms = float(np.sqrt(np.mean(np.square(block))))
        events.append(VoiceEvent(kind="level", rms=round(rms, 4)))

    # -- sleeping -------------------------------------------------------

    def _listen_for_wake(self, block: np.ndarray, events: list[VoiceEvent]) -> None:
        if self._wake is None:
            return
        self._wake_buffer = np.concatenate([self._wake_buffer, block])
        while self._wake_buffer.size >= wake_module.CHUNK:
            chunk = self._wake_buffer[: wake_module.CHUNK]
            self._wake_buffer = self._wake_buffer[wake_module.CHUNK :]
            score = self._wake.feed(chunk)
            if score is None or self._cooldown_samples > 0:
                continue
            if score >= self.cfg.wake.threshold:
                events.append(VoiceEvent(kind="wake", score=round(score, 4)))
                self.open(from_wake=True)
                return

    def expect_follow_up(self) -> None:
        """Leave the door open for a while. Called when a reply finishes.

        Saying the wake phrase before every single question turns a
        conversation into a sequence of summonings. So for `follow_up_ms`
        after a reply, speech alone opens a clip -- the same test barge-in
        uses, which is the same question either way: is that a person talking
        to me, or is that a room.
        """
        self._follow_up = self._samples(self.cfg.wake.follow_up_ms)

    def _open_on_speech(
        self, block: np.ndarray, events: list[VoiceEvent], *, kind: str
    ) -> None:
        """Open a clip on sustained speech, with no wake word in the way.

        Barge-in is the latency-sensitive use: nothing has to wake up for it,
        because the VAD has been running the whole time, which is why the
        target is under 100 ms at all. The follow-up window is the same
        machinery at the ordinary threshold.
        """
        if self._vad is None:
            return
        self._vad_buffer = np.concatenate([self._vad_buffer, block])
        needed = max(
            1, round(self.cfg.echo.barge_in_ms / (vad_module.WINDOW * 1000 / self.rate))
        )
        floor = self.reference_rms * self.cfg.echo.barge_in_ratio
        while self._vad_buffer.size >= vad_module.WINDOW:
            window = self._vad_buffer[: vad_module.WINDOW]
            self._vad_buffer = self._vad_buffer[vad_module.WINDOW :]
            speech = self._vad.probability(window) >= self._threshold()
            # Loud enough, against what is being played, to be a person. The
            # VAD cannot answer this: Sunday's own voice returning through the
            # room *is* speech, and Silero scores it 1.0. Raising the VAD
            # threshold does not help for the same reason. What separates them
            # is that a person is loud where the residual echo is not.
            loud = float(np.sqrt(np.mean(np.square(window)))) >= floor
            if speech and loud:
                self._burst += 1
            else:
                self._burst = 0
                continue
            if self._burst >= needed:
                events.append(VoiceEvent(kind=kind))  # type: ignore[arg-type]
                self._burst = 0
                self._follow_up = 0
                self.open()
                return

    def open(self, *, from_wake: bool = False) -> None:
        """Start recording without waiting for the phrase.

        The global hotkey and the mic button both land here. `from_wake` only
        decides whether the wake detector's own context is thrown away, which
        it must be after a trigger or the phrase is still in the 1.28 s window
        when listening resumes and fires again.
        """
        self.phase = "recording"
        self._clip = []
        self._clip_samples = 0
        self._speech_samples = 0
        self._silence_samples = 0
        self._windows = 0
        self._first_speech = None
        self._last_speech = None
        self._live_speech = 0
        self._preroll_samples = 0
        self._burst = 0
        self._overlapped_speech = self.speaking
        self._vad_buffer = np.zeros(0, dtype=np.float32)
        self._cooldown_samples = self._samples(self.cfg.wake.cooldown_ms)
        if self._vad is not None:
            self._vad.reset()
        if from_wake and self._wake is not None:
            self._wake.reset()

        # The pre-roll goes through the VAD like everything else. It has to:
        # the clip is trimmed to where the speech is, and half a second of
        # audio nobody looked at is half a second that can only be cut.
        preroll = list(self._preroll)
        self._preroll.clear()
        for part in preroll:
            self._absorb(part, live=False)
        self._preroll_samples = self._clip_samples

    # -- recording ------------------------------------------------------

    def _threshold(self) -> float:
        """While Sunday is speaking, residual echo is quiet and choppy and a
        person interrupting is loud and continuous. So the bar goes up."""
        if self.speaking:
            return self.cfg.echo.speech_threshold_while_speaking
        return self.cfg.audio.vad_threshold

    def _absorb(self, block: np.ndarray, *, live: bool = True) -> None:
        """Add a block to the clip and let the VAD say what is in it.

        Positions matter as much as counts here. The clip handed over is
        trimmed to the speech, because Moonshine was trained on segments that
        were trimmed that way and answers a clip padded with silence by
        emitting end-of-sequence as its very first token -- an empty
        transcript for audio that plainly has words in it.
        """
        self._clip.append(block)
        self._clip_samples += block.size
        if self.speaking:
            self._overlapped_speech = True

        self._vad_buffer = np.concatenate([self._vad_buffer, block])
        threshold = self._threshold()
        while self._vad_buffer.size >= vad_module.WINDOW:
            window = self._vad_buffer[: vad_module.WINDOW]
            self._vad_buffer = self._vad_buffer[vad_module.WINDOW :]
            speech = self._vad is not None and self._vad.probability(window) >= threshold
            at = self._windows * vad_module.WINDOW
            self._windows += 1
            if speech:
                self._speech_samples += vad_module.WINDOW
                self._silence_samples = 0
                if live:
                    self._live_speech += vad_module.WINDOW
                if self._first_speech is None:
                    self._first_speech = at
                self._last_speech = at + vad_module.WINDOW
            else:
                self._silence_samples += vad_module.WINDOW

    def _record(self, block: np.ndarray, events: list[VoiceEvent]) -> None:
        self._absorb(block)

        if self._clip_samples >= self._samples(self.cfg.audio.max_clip_ms):
            events.append(self._close(forced=True))
            return

        # Nothing said *yet*. The silence that ends a clip is 700 ms and the
        # pause between "hey jarvis" and the question is routinely twice that,
        # so until the question actually starts, the only clock running is the
        # one that gives up entirely.
        #
        # "Started" is `min_clip_ms` of live speech, not one window of it. A
        # VAD window straddling the wake word carries the tail of the phrase
        # into the live count, and one 32 ms window was enough to start the
        # stopwatch that ends the clip -- the same failure the pre-roll flag
        # was added to fix, arriving through the boundary between them. The
        # floor that decides a cough is not a coincidence here: it is the same
        # question asked at the other end, which is whether that was a person
        # talking to you.
        if self._live_speech < self._samples(self.cfg.audio.min_clip_ms):
            since_wake = self._clip_samples - self._preroll_samples
            if self._ms(since_wake) >= self.cfg.audio.lead_in_ms:
                self._reset()
                events.append(VoiceEvent(kind="dropped", why="nothing was said"))
            return

        if self._ms(self._silence_samples) >= self.cfg.audio.vad_silence_ms:
            events.append(self._close(forced=False))

    def _close(self, *, forced: bool) -> VoiceEvent:
        audio = (
            np.concatenate(self._clip) if self._clip else np.zeros(0, dtype=np.float32)
        )
        audio = self._trim(audio)
        speech_ms = self._ms(self._speech_samples)
        total_ms = self._ms(audio.size)
        overlapped = self._overlapped_speech
        self._reset()
        if speech_ms < self.cfg.audio.min_clip_ms:
            return VoiceEvent(kind="dropped", why=f"only {speech_ms} ms of speech")
        return VoiceEvent(
            kind="clip",
            clip=Clip(
                audio=audio,
                speech_ms=speech_ms,
                total_ms=total_ms,
                forced=forced,
                while_speaking=overlapped,
            ),
        )

    def _trim(self, audio: np.ndarray) -> np.ndarray:
        """Cut the clip down to the speech, plus a margin either side.

        Not an optimisation. Moonshine returns an empty string for a clip
        wrapped in silence -- measured on this build: two seconds of padding
        either side of four seconds of clear speech, and end-of-sequence
        outscores the first real word. Every clip here carries half a second
        of pre-roll and seven-tenths of trailing silence by construction, so
        without this the common case is the failing one.
        """
        if self._first_speech is None or self._last_speech is None:
            return audio
        margin = self._samples(TRIM_MARGIN_MS)
        start = max(0, self._first_speech - margin)
        end = min(audio.size, self._last_speech + margin)
        return audio[start:end] if end > start else audio

    def _reset(self) -> None:
        self.phase = "sleeping"
        self._clip = []
        self._clip_samples = 0
        self._speech_samples = 0
        self._silence_samples = 0
        self._windows = 0
        self._first_speech = None
        self._last_speech = None
        self._live_speech = 0
        self._preroll_samples = 0
        self._burst = 0
        self._follow_up = 0
        self._overlapped_speech = False
        self._vad_buffer = np.zeros(0, dtype=np.float32)
        self._wake_buffer = np.zeros(0, dtype=np.float32)
        self._preroll.clear()
        if self._wake is not None:
            self._wake.reset()

    def abandon(self) -> None:
        """Throw away whatever is being recorded and go back to sleep."""
        self._reset()


__all__ = ["Clip", "FRAME_MS", "Listener", "VoiceEvent"]

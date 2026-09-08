"""The mouth: sentences in, sound out, and stop the instant you are told.

Two threads, and the split between them is the whole design. One synthesises,
one plays. Sentence two is being made while sentence one is still coming out of
the speaker, so the only synthesis you ever wait for is the first -- about a
second here, hidden behind the fact that the model is still writing anyway.
Run both on one thread and every sentence boundary becomes an audible gap the
length of a synthesis, which is worse than the batching this design exists to
avoid: at least batching only makes you wait once.

Three things this has to get right, and only one of them is playback.

**Stopping has to be instant, and it has to stay stopped for exactly one turn.**
Barge-in targets under 100 ms from your first syllable to silence, and most of
the delay in a naive implementation is audio already handed to the sound card.
So the stream is fed in small blocks rather than pushed whole, and `stop()`
drops both queues, abandons whatever is in flight, and lets the current block
finish and no more. Which turn a sentence belongs to is a number rather than a
flag: a latched flag either stops the next turn as well or has to be cleared by
whoever speaks next, and both of those are races.

**What was played has to be reported.** The echo canceller needs the exact
samples that went to the speaker, and it is the only reference it will get.
Because Sunday synthesised them, that reference is bit-exact -- the easy case
for echo cancellation, and the reason layer 1 is worth having at all.

**`speaking` has to stay true across the gap between sentences.** It is what
raises the VAD bar, and dropping it for the tenth of a second between two
sentences opens a hole in layer 2 exactly where the echo is loudest.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable

import numpy as np

from sunday import config
from sunday.audio.tts import RATE, Speech

#: What goes to the sound card at a time. 20 ms at 24 kHz, matching the capture
#: frame, so the echo canceller sees both sides on the same cadence.
BLOCK = 480

#: Sentences synthesised ahead of the one playing. One is enough to cover the
#: gap and keeps the memory bounded; more would only buy latency you cannot
#: hear and audio you may be about to throw away.
LOOKAHEAD = 2

#: How long `close()` waits for each thread. They have at most one block and
#: one sentence left, so this is a fault timeout rather than a delay.
STOP_TIMEOUT_S = 5.0

PlayedSink = Callable[[np.ndarray], None]
StateSink = Callable[[str, str], None]


class Speaker:
    """Everything from a sentence down to the sound card."""

    def __init__(
        self,
        cfg: config.Config | None = None,
        *,
        speech: Speech | None = None,
        on_played: PlayedSink | None = None,
        on_state: StateSink | None = None,
    ) -> None:
        self.cfg = cfg or config.get()
        self._speech = speech
        self._on_played = on_played
        self._on_state = on_state

        self._to_say: queue.Queue[tuple[int, str] | None] = queue.Queue()
        self._to_play: queue.Queue[tuple[int, str, np.ndarray] | None] = queue.Queue(
            maxsize=LOOKAHEAD
        )
        self._threads: list[threading.Thread] = []
        self._stream: Any = None
        self._lock = threading.Lock()
        #: Which turn is speaking. `stop()` moves it on, and anything carrying
        #: the old number is thrown away wherever it is noticed.
        self._turn = 0
        #: How many sentences of this turn are queued, being synthesised, or
        #: playing. `speaking` is this being non-zero, which is what keeps the
        #: VAD bar up across the gap between two sentences.
        self._outstanding = 0
        #: The sentence coming out of the speaker right now, for layer 3.
        self.saying = ""
        self.error: str | None = None

    @property
    def speaking(self) -> bool:
        with self._lock:
            return self._outstanding > 0

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self._threads:
            return
        for name, target in (
            ("sunday-synth", self._synthesise),
            ("sunday-play", self._play),
        ):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def close(self) -> None:
        self.stop()
        self._to_say.put(None)
        self._to_play.put(None)
        for thread in self._threads:
            thread.join(timeout=STOP_TIMEOUT_S)
        self._threads = []
        self._close_stream()

    # -- what a turn does -----------------------------------------------

    def say(self, sentence: str) -> None:
        """Queue one sentence. Returns immediately; the threads do the work."""
        if not sentence.strip():
            return
        with self._lock:
            turn = self._turn
            self._outstanding += 1
        self._to_say.put((turn, sentence))

    def stop(self) -> None:
        """Barge-in, cancellation, or the end of a turn nobody wants.

        Drops both queues and everything in flight. The block already handed to
        the sound card finishes -- that is 20 ms and it is the floor.
        """
        with self._lock:
            self._turn += 1
            self._outstanding = 0
        for pipe in (self._to_say, self._to_play):
            while True:
                try:
                    item = pipe.get_nowait()
                except queue.Empty:
                    break
                if item is None:  # a close request; do not eat it
                    pipe.put(None)
                    break
        self._quiet()

    def _current(self) -> int:
        with self._lock:
            return self._turn

    def _done_with_one(self) -> None:
        with self._lock:
            self._outstanding = max(0, self._outstanding - 1)
            left = self._outstanding
        if left == 0:
            self._quiet()

    # -- thread one: making it ------------------------------------------

    def _synthesise(self) -> None:
        while True:
            item = self._to_say.get()
            if item is None:
                self._to_play.put(None)
                return
            turn, sentence = item
            if turn != self._current():
                continue

            try:
                if self._speech is None:
                    self._speech = Speech(self.cfg)
                audio = self._speech.say(sentence)
            except Exception as exc:  # a missing model, a broken voice pack
                self.error = f"could not speak that: {exc}"
                self._say("error", self.error)
                self._done_with_one()
                continue

            if audio.size == 0 or turn != self._current():
                self._done_with_one()
                continue
            self._to_play.put((turn, sentence, audio))

    # -- thread two: playing it -----------------------------------------

    def _play(self) -> None:
        while True:
            item = self._to_play.get()
            if item is None:
                return
            turn, sentence, audio = item
            if turn != self._current():
                self._done_with_one()
                continue

            self.saying = sentence
            self._say("speaking", sentence)
            try:
                stream = self._open()
                for start in range(0, audio.size, BLOCK):
                    if turn != self._current():
                        break
                    block = audio[start : start + BLOCK]
                    if self._on_played is not None:
                        self._on_played(block)
                    stream.write(block)
            except Exception as exc:  # the output device went away
                self.error = f"could not play that: {exc}"
                self._say("error", self.error)
            self._done_with_one()

    def _quiet(self) -> None:
        if not self.saying:
            return
        self.saying = ""
        self._say("idle", "")

    def _say(self, state: str, text: str) -> None:
        if self._on_state is not None:
            self._on_state(state, text)

    # -- the device -----------------------------------------------------

    def _open(self) -> Any:
        if self._stream is not None:
            return self._stream
        from sunday.audio.capture import _sounddevice

        sd = _sounddevice()
        name = self.cfg.audio.output_device.strip()
        self._stream = sd.OutputStream(
            samplerate=RATE,
            channels=1,
            dtype="float32",
            blocksize=BLOCK,
            device=name or None,
        )
        self._stream.start()
        return self._stream

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass


__all__ = ["BLOCK", "LOOKAHEAD", "Speaker"]

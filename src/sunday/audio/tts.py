"""Kokoro: one sentence in, one buffer of speech out.

This is the one component in the package that keeps its own library, and the
reason is the half that is not the model. Kokoro takes phonemes, not text, and
getting from English to phonemes is espeak-ng -- a C library with a
pronunciation dictionary, not a graph you can run. `kokoro-onnx` brings that
binding and the token mapping and nothing else heavy: espeak-ng's loader,
phonemizer, numpy and onnxruntime, all of which are here already or weigh
nothing. Reimplementing it would be reimplementing a dictionary.

The unit of work is one sentence, because that is what `stream.SentenceSplitter`
produces and what the whole streaming argument rests on: Kokoro says sentence
one while the agent is still writing sentence two. Synthesis runs at three to
four times real time on this CPU, so a two-second sentence costs about half a
second -- which is hidden entirely as long as it is never asked for the whole
reply at once.

Output is 24 kHz mono float32. The microphone is 16 kHz, and the echo canceller
needs both in the same clock, which is `aec.to_capture_rate`'s job rather than
this file's -- the speaker gets the good version.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from sunday import config
from sunday.audio import models, onnx

#: Kokoro's own output rate. Not configurable -- it is what the model produces.
RATE = 24000



class Speech:
    """Loaded once and kept resident, like everything else here."""

    def __init__(self, cfg: config.Config | None = None) -> None:
        from kokoro_onnx import Kokoro

        self.cfg = cfg or config.get()

        # The one model allowed the whole machine, because it is the one whose
        # latency you hear. It may not hold the cores hot, though -- see onnx.
        self._kokoro: Any = Kokoro.from_session(
            onnx.session(models.model_path("kokoro/kokoro-v1.0.onnx"), threads=None),
            str(models.model_path("kokoro/voices-v1.0.bin")),
        )
        self.voice = self.cfg.tts.voice
        self.speed = self.cfg.tts.speed

    def voices(self) -> list[str]:
        return sorted(self._kokoro.get_voices())

    def say(self, sentence: str) -> np.ndarray:
        """One sentence as 24 kHz mono float32. Empty if there is nothing in it.

        A sentence that is only punctuation happens more often than it sounds:
        the splitter emits whatever is left when the stream ends, and a reply
        that ends mid-token leaves a stray mark behind.
        """
        text = sentence.strip()
        if not any(character.isalnum() for character in text):
            return np.zeros(0, dtype=np.float32)

        audio, rate = self._kokoro.create(
            text, voice=self.voice, speed=self.speed, lang=self.cfg.tts.language
        )
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        if rate != RATE:  # never seen, but a silent rate change would be ugly
            raise RuntimeError(f"Kokoro returned {rate} Hz, expected {RATE}")
        return samples


__all__ = ["RATE", "Speech"]

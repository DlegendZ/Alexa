"""The wake word: openWakeWord's three ONNX models, run directly.

openWakeWord is a package as well as a set of models, and the package brings
scikit-learn and a TensorFlow-Lite runtime with it. What it actually does at
inference is three small ONNX graphs in a row, which is what this file is:

    audio -> melspectrogram -> embedding -> one probability

The shapes are fixed by the models and are not negotiable. Melspectrogram
takes raw int16-scale samples and returns 32 mel bins per 10 ms hop. The
embedding model takes a 76-frame mel window and returns 96 numbers. The wake
model reads the last 16 of those embeddings -- 1.28 s of context -- and
returns one score between 0 and 1.

The unit of work is 1280 samples, 80 ms: exactly 8 mel hops, exactly one new
embedding. Feeding anything else still works, but 80 ms is the step the models
were trained on and is what the ring is sized around.

Threshold and cooldown live in `[wake]` in config. The cooldown is not a
detail: at 80 ms a step, one spoken phrase scores above threshold for a dozen
consecutive steps, and without it a single "hey jarvis" opens a dozen turns.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sunday.audio import models, onnx

#: The step the models were trained on: 80 ms at 16 kHz.
CHUNK = 1280

#: Mel frames the embedding model wants.
_MEL_WINDOW = 76

#: Embeddings the wake model reads: 16 x 80 ms of context.
_EMBEDDINGS = 16

#: openWakeWord's own normalisation of the mel output. Not a tuning knob --
#: the embedding model was trained on melspectrograms scaled this way.
_MEL_SCALE = 10.0
_MEL_SHIFT = 2.0

#: The melspectrogram graph needs a little history to fill its first frames.
_MEL_LOOKBACK = 480


@dataclass
class Detection:
    score: float
    phrase: str


class WakeWord:
    """Always on, on the CPU, and cheap enough to leave that way.

    Roughly 1 ms per 80 ms step on one thread, which is the whole reason the
    microphone can be open all the time without the fans coming on.
    """

    def __init__(self, phrase: str = "hey_jarvis", *, threshold: float = 0.5) -> None:
        self.phrase = phrase
        self.threshold = threshold

        # One thread each. Three tiny graphs run every 80 ms forever, and
        # starting a pool for them costs more than it saves.
        self._mel = onnx.session(models.model_path("wake/melspectrogram.onnx"))
        self._embed = onnx.session(models.model_path("wake/embedding_model.onnx"))
        self._model = onnx.session(models.model_path(models.wake_key(phrase)))

        # Every input name is read from the graph rather than written down.
        # The three pretrained phrases were exported at different times and do
        # not agree: `hey_jarvis` calls its input `x.1`, while `alexa` and
        # `hey_mycroft` call theirs `onnx::Flatten_0`. A name written into this
        # file works for whichever phrase it was written against and raises
        # `Required inputs are missing from input feed` for the others -- on
        # the audio thread, the first time somebody says the word.
        self._mel_input = self._mel.get_inputs()[0].name
        self._embed_input = self._embed.get_inputs()[0].name
        self._model_input = self._model.get_inputs()[0].name

        self._raw = np.zeros(0, dtype=np.float32)
        self._mels = np.zeros((0, 32), dtype=np.float32)
        self._embeddings = np.zeros((0, 96), dtype=np.float32)

    # -- the pipeline ---------------------------------------------------

    def reset(self) -> None:
        """Forget the context. Called after a trigger so the phrase that fired
        cannot still be sitting in the window when listening resumes."""
        self._raw = np.zeros(0, dtype=np.float32)
        self._mels = np.zeros((0, 32), dtype=np.float32)
        self._embeddings = np.zeros((0, 96), dtype=np.float32)

    def feed(self, samples: np.ndarray) -> float | None:
        """One 80 ms step. Returns a score, or None while the window fills.

        Samples arrive as float32 in [-1, 1] -- what every other part of this
        package deals in -- and are scaled back to int16 range here, because
        that is what the melspectrogram graph was exported to expect.
        """
        block = np.asarray(samples, dtype=np.float32) * 32768.0
        self._raw = np.concatenate([self._raw, block])[-16000 * 4 :]

        window = self._raw[-(len(block) + _MEL_LOOKBACK) :]
        mel = self._mel.run(None, {self._mel_input: window[None, :]})[0]
        mel = np.squeeze(mel) / _MEL_SCALE + _MEL_SHIFT
        fresh = max(len(block) // 160, 1)
        self._mels = np.vstack([self._mels, mel[-fresh:]])[-200:]

        if self._mels.shape[0] < _MEL_WINDOW:
            return None

        patch = self._mels[-_MEL_WINDOW:][None, :, :, None]
        embedding = self._embed.run(None, {self._embed_input: patch})[0].reshape(1, 96)
        self._embeddings = np.vstack([self._embeddings, embedding])[-32:]

        if self._embeddings.shape[0] < _EMBEDDINGS:
            return None

        context = self._embeddings[-_EMBEDDINGS:][None, :, :]
        score = self._model.run(None, {self._model_input: context})[0]
        return float(score[0][0])

    def heard(self, samples: np.ndarray) -> Detection | None:
        score = self.feed(samples)
        if score is None or score < self.threshold:
            return None
        return Detection(score=score, phrase=self.phrase)


__all__ = ["CHUNK", "Detection", "WakeWord"]

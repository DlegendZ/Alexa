"""Silero VAD: where speech starts and where it stops.

Moonshine does not stream. It transcribes a clip you hand it, so something has
to decide where that clip begins and ends -- and later, while Kokoro is
speaking, the same component is what notices you talking over it. Both jobs
are one 2 MB ONNX graph.

Two things about the model are easy to get wrong and silent when you do:

- It takes exactly 512 samples at 16 kHz. Not 320, not 480.
- Since v5 it also wants the 64 samples immediately *before* those 512, fed
  as context. Leave them out and it loads, runs, and returns 0.02 for clear
  speech -- which reads as a broken microphone rather than a broken call.

It carries state between chunks, so the frames must arrive in order and
`reset()` is what starts a new utterance.
"""

from __future__ import annotations

import numpy as np

from sunday.audio import models

#: The only window size the model accepts at 16 kHz.
WINDOW = 512

#: Samples of the previous window handed back as context. Silero v5 onwards.
_CONTEXT = 64


class Vad:
    """One probability per 512 samples, and the state that ties them together."""

    def __init__(self, *, sample_rate: int = 16000) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(models.model_path("vad/silero_vad.onnx")),
            options,
            providers=["CPUExecutionProvider"],
        )
        self._sr = np.array(sample_rate, dtype=np.int64)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(_CONTEXT, dtype=np.float32)

    def reset(self) -> None:
        """Start a fresh utterance. Nothing carries over."""
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(_CONTEXT, dtype=np.float32)

    def probability(self, window: np.ndarray) -> float:
        """How likely `window` -- exactly `WINDOW` samples -- is speech."""
        chunk = np.asarray(window, dtype=np.float32)
        if chunk.shape[-1] != WINDOW:
            raise ValueError(f"Silero wants {WINDOW} samples, got {chunk.shape[-1]}")
        fed = np.concatenate([self._context, chunk])[None, :]
        out, self._state = self._session.run(
            None, {"input": fed, "state": self._state, "sr": self._sr}
        )
        self._context = chunk[-_CONTEXT:]
        return float(out[0][0])


__all__ = ["WINDOW", "Vad"]

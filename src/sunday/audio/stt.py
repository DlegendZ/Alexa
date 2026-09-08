"""Moonshine: one closed clip becomes one line of text.

An encoder-decoder run directly on onnxruntime, which is the whole of the
dependency: no torch, no transformers, no ctranslate2. The encoder reads the
waveform -- there is no mel step, Moonshine takes samples -- and the decoder
generates greedily until it emits end-of-sequence.

Two things about the merged decoder export cost an afternoon each, so they are
written down here rather than discovered again:

- The dummy past on the first pass must be zero *length*, shape (1, 8, 0, 52).
  A length of 1 loads, runs the first token, and then fails inside the If node
  with a broadcast error that names a matmul and not the cause.
- On every pass after the first the cross-attention K/V comes back *empty* --
  shape (0, 8, 1, 52) -- because it cannot have changed. Feed those back and
  the batch dimension collapses to zero. Compute them once on the first pass
  and hold them for the rest of the clip.

Measured on this machine, CPU, base float: 2.5 s of speech is ~15 ms of
encoder and ~210 ms of decoding. That is the gap between you stopping and the
first token, and it is why the orb needs a transcribing state.
"""

from __future__ import annotations

import numpy as np

from sunday.audio import models

#: Decoder start, and end of sequence, from Moonshine's generation config.
_BOS = 1
_EOS = 2

#: Moonshine's own cap. A 30 s clip cannot produce more words than this.
_MAX_TOKENS = 194

#: Below this there is nothing to transcribe -- the encoder needs enough
#: samples to produce at least one frame.
MIN_SAMPLES = 1600


class Moonshine:
    """Loaded once and kept resident. Nothing here sleeps."""

    def __init__(self) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 4

        self._encoder = ort.InferenceSession(
            str(models.model_path("moonshine/encoder_model.onnx")),
            options,
            providers=["CPUExecutionProvider"],
        )
        self._decoder = ort.InferenceSession(
            str(models.model_path("moonshine/decoder_model_merged.onnx")),
            options,
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = Tokenizer.from_file(str(models.model_path("moonshine/tokenizer.json")))

        self._past = [i.name for i in self._decoder.get_inputs() if i.name.startswith("past_key_values")]
        self._outputs = [o.name for o in self._decoder.get_outputs()]
        self._empty = np.zeros((1, 8, 0, 52), dtype=np.float32)

    def transcribe(self, audio: np.ndarray) -> str:
        """Float32 mono at 16 kHz in, one line of text out."""
        clip = np.asarray(audio, dtype=np.float32).reshape(1, -1)
        if clip.shape[1] < MIN_SAMPLES:
            return ""

        hidden = self._encoder.run(None, {"input_values": clip})[0]

        feed = {name: self._empty for name in self._past}
        feed["input_ids"] = np.array([[_BOS]], dtype=np.int64)
        feed["encoder_hidden_states"] = hidden
        feed["use_cache_branch"] = np.array([False])

        cross: dict[str, np.ndarray] = {}
        tokens: list[int] = []
        for _ in range(_MAX_TOKENS):
            outputs = self._decoder.run(None, feed)
            nxt = int(outputs[0][0, -1].argmax())
            if nxt == _EOS:
                break
            tokens.append(nxt)

            present = dict(zip(self._outputs, outputs))
            feed = {}
            for name in self._past:
                fresh = present[name.replace("past_key_values", "present")]
                if ".encoder." in name:
                    # Unchanged for the whole clip, and returned empty from
                    # here on. Keep the first pass's and stop asking.
                    feed[name] = cross.setdefault(name, fresh)
                else:
                    feed[name] = fresh
            feed["input_ids"] = np.array([[nxt]], dtype=np.int64)
            feed["encoder_hidden_states"] = hidden
            feed["use_cache_branch"] = np.array([True])

        return self._tokenizer.decode(tokens).strip()


__all__ = ["MIN_SAMPLES", "Moonshine"]

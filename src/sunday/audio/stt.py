"""Transcription: one closed clip becomes one line of text.

Two transcribers live here and `[models] stt` picks between them. Both take
float32 mono at 16 kHz and return a string, and neither knows anything about
the rest of the pipeline.

**Parakeet TDT 0.6B**, the default. A FastConformer encoder with a token-and-
duration transducer on top, run through `onnx-asr` -- which is the third
library in this package with a good excuse, and the excuse is the same as
Kokoro's. What it supplies is not the model but the machinery around it: a mel
front-end exported as its own graph, and a TDT decode loop that emits a token
and a duration together and skips frames accordingly. That is a specific
algorithm, not glue, and it brings nothing heavy with it -- numpy,
onnxruntime and huggingface-hub, all of which are here already.

**Moonshine base**, kept and still selectable. An encoder-decoder run directly
on onnxruntime, no library at all, and by the measurements below it is the
faster of the two by a wide margin.

The comparison, on eight labelled clips, clean and with noise added:

    model                   WER    ms/clip   at 15 dB   at 8 dB
    moonshine base          0.0%      123      0.0%       0.0%
    parakeet tdt 0.6b int8  2.6%      704      2.6%       2.6%

Moonshine wins that on its face, and the single disagreement across the eight
was Parakeet joining a filename -- "quarterlynotes.txt" for "quarterly
notes.txt" -- which matters for an assistant that opens files by name. What
the table cannot show is the reason for the change: the set is synthetic
speech from one voice, and on the one real recording available Parakeet was
the only model of four to get the wake phrase nearly right. It is also the
stronger model by every published benchmark, and it is a 600M-parameter
encoder against Moonshine base's 61M.

So the trade being made is roughly 580 ms of extra latency before the first
token of every spoken turn, against a model with far more headroom on speech
this set does not contain. `[models] stt = "moonshine-base"` goes back.

Both refuse a clip too short to hold a word rather than guessing at it.
"""

from __future__ import annotations

from typing import Any, Protocol

import numpy as np

from sunday import config
from sunday.audio import models, onnx

#: Below this there is nothing to transcribe -- the encoders need enough
#: samples to produce at least one frame. Parakeet will hallucinate a filler
#: word for a clip of 100 ms; at any length the listener would actually send,
#: both models return an empty string for silence, noise and a thump.
MIN_SAMPLES = 1600


class Transcriber(Protocol):
    """Float32 mono at 16 kHz in, one line of text out."""

    def transcribe(self, audio: np.ndarray) -> str: ...


class Parakeet:
    """NVIDIA Parakeet TDT 0.6B, int8, on the CPU."""

    def __init__(self, cfg: config.Config | None = None) -> None:
        import onnx_asr

        self.cfg = cfg or config.get()
        # Ensure every file is present before handing the directory over, so a
        # partial download fails here with a name and a command rather than
        # inside the loader with a stack trace.
        for key in models.PARAKEET:
            models.model_path(key)

        self._model: Any = onnx_asr.load_model(
            "nemo-conformer-tdt",
            models.path_for(models.PARAKEET[0]).parent,
            quantization="int8",
            sess_options=onnx.options(threads=4),
        )

    def transcribe(self, audio: np.ndarray) -> str:
        clip = np.asarray(audio, dtype=np.float32).reshape(-1)
        if clip.size < MIN_SAMPLES:
            return ""
        return str(self._model.recognize(clip, sample_rate=16000)).strip()


class Moonshine:
    """Moonshine base, run directly on onnxruntime.

    Two things about the merged decoder export cost an afternoon each, so they
    are written down here rather than discovered again:

    - The dummy past on the first pass must be zero *length*, shape
      (1, 8, 0, 52). A length of 1 loads, runs the first token, and then fails
      inside the If node with a broadcast error that names a matmul and not
      the cause.
    - On every pass after the first the cross-attention K/V comes back *empty*
      -- shape (0, 8, 1, 52) -- because it cannot have changed. Feed those
      back and the batch dimension collapses to zero. Compute them once on the
      first pass and hold them for the rest of the clip.
    """

    #: Decoder start, and end of sequence, from the generation config.
    _BOS = 1
    _EOS = 2

    #: Moonshine's own cap. A 30 s clip cannot produce more words than this.
    _MAX_TOKENS = 194

    def __init__(self) -> None:
        from tokenizers import Tokenizer

        # Four threads: enough that a thirty-second clip is not a wait, few
        # enough that transcribing does not stop the microphone hearing the
        # next thing you say.
        self._encoder = onnx.session(
            models.model_path("moonshine/encoder_model.onnx"), threads=4
        )
        self._decoder = onnx.session(
            models.model_path("moonshine/decoder_model_merged.onnx"), threads=4
        )
        self._tokenizer = Tokenizer.from_file(
            str(models.model_path("moonshine/tokenizer.json"))
        )

        self._past = [
            i.name
            for i in self._decoder.get_inputs()
            if i.name.startswith("past_key_values")
        ]
        self._outputs = [o.name for o in self._decoder.get_outputs()]
        self._empty = np.zeros((1, 8, 0, 52), dtype=np.float32)

    def transcribe(self, audio: np.ndarray) -> str:
        clip = np.asarray(audio, dtype=np.float32).reshape(1, -1)
        if clip.shape[1] < MIN_SAMPLES:
            return ""

        hidden = self._encoder.run(None, {"input_values": clip})[0]

        feed = {name: self._empty for name in self._past}
        feed["input_ids"] = np.array([[self._BOS]], dtype=np.int64)
        feed["encoder_hidden_states"] = hidden
        feed["use_cache_branch"] = np.array([False])

        cross: dict[str, np.ndarray] = {}
        tokens: list[int] = []
        for _ in range(self._MAX_TOKENS):
            outputs = self._decoder.run(None, feed)
            nxt = int(outputs[0][0, -1].argmax())
            if nxt == self._EOS:
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


def load(cfg: config.Config | None = None) -> Transcriber:
    """Whichever transcriber `[models] stt` names."""
    cfg = cfg or config.get()
    if cfg.models.stt == "moonshine-base":
        return Moonshine()
    return Parakeet(cfg)


__all__ = ["MIN_SAMPLES", "Moonshine", "Parakeet", "Transcriber", "load"]

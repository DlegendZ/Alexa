"""Layer 1: subtract what the speaker played from what the microphone heard.

This is the ideal case for echo cancellation and it is worth saying why. Most
cancellers have to guess what came out of the speaker; here Sunday synthesised
it, so the reference is the exact buffer that was handed to the sound card.
What is left to remove is the room -- reflection, speaker colouration, the path
from one to the other -- which is precisely what an adaptive filter is good at.

Written in numpy rather than bound to speexdsp or webrtc-audio-processing,
because neither publishes a Windows wheel and both want a C toolchain to build.
Asking someone to install MSVC before their assistant can talk is a worse trade
than eighty lines of arithmetic. What is here is a partitioned block frequency-
domain adaptive filter: overlap-save, normalised, `PARTITIONS` blocks of tail,
one FFT pair per 20 ms frame. It costs about a fifth of a millisecond a frame.

Two things it is not. It is not a noise suppressor -- it removes what was
played and nothing else. And it is not a substitute for layers 2 and 3: an
adaptive filter needs a few hundred milliseconds to converge after the path
changes, and the first syllable of a reply is inside that window.

The reference arrives at 24 kHz, because that is what Kokoro produces, and the
microphone runs at 16 kHz. They have to share a clock before anything can be
subtracted, which is `to_capture_rate` below -- an exact 2-up, 3-down rational
resample, not an interpolation.
"""

from __future__ import annotations

import numpy as np

#: One capture frame: 20 ms at 16 kHz.
FRAME = 320

#: How much room the filter can model. Eight frames is 160 ms of tail, which
#: covers a desk and a small room. More is slower to converge, not better.
PARTITIONS = 8

#: Adaptation rate. High enough to follow a path that changes when you move,
#: low enough not to chase the near-end talker.
MU = 0.3

#: Keeps the normalisation honest when the reference is silent.
EPSILON = 1e-8


def to_capture_rate(block: np.ndarray) -> np.ndarray:
    """24 kHz to 16 kHz, the exact ratio, without pulling in a resampler.

    Two up and three down. The zero-stuffing doubles the rate and mirrors the
    spectrum above 12 kHz; the filter removes that image and everything above
    8 kHz in one pass, so the decimation has nothing left to alias.
    """
    if block.size == 0:
        return block.astype(np.float32)
    upsampled = np.zeros(block.size * 2, dtype=np.float32)
    upsampled[::2] = np.asarray(block, dtype=np.float32) * 2.0
    filtered = np.convolve(upsampled, _LOWPASS, mode="same")
    return filtered[::3].astype(np.float32)


def _windowed_sinc(taps: int, cutoff: float) -> np.ndarray:
    """A linear-phase low pass, cutoff in cycles per sample of the 48 kHz grid."""
    n = np.arange(taps) - (taps - 1) / 2.0
    kernel = np.sinc(2 * cutoff * n) * np.hamming(taps)
    return (kernel / kernel.sum()).astype(np.float32)


#: 8 kHz in a 48 kHz intermediate: everything the 16 kHz output can hold.
_LOWPASS = _windowed_sinc(63, 8000 / 48000)


class Aec:
    """One frame in, one frame out, with the echo taken off it.

    Feed it every reference block as it goes to the speaker and every capture
    frame as it arrives. It aligns them itself using the configured delay --
    which is hardware, has to be measured once, and is the one number here that
    cannot be derived.
    """

    def __init__(
        self,
        *,
        frame: int = FRAME,
        partitions: int = PARTITIONS,
        delay_ms: int = 60,
        rate: int = 16000,
        mu: float = MU,
    ) -> None:
        self.frame = frame
        self.partitions = partitions
        self.rate = rate
        self.mu = mu
        self._fft = frame * 2

        #: The reference, at capture rate, waiting to be lined up. Held as one
        #: array rather than a deque of blocks because the two sides arrive in
        #: different sized pieces and only the sample count is meaningful.
        self._reference = np.zeros(0, dtype=np.float32)
        #: How much reference to hold back, so that the frame subtracted from
        #: the microphone is the one that was in the air at the time.
        self._delay = max(0, round(delay_ms * rate / 1000))
        #: The tail of the reference, oldest first, as spectra.
        self._history = np.zeros((partitions, self._fft // 2 + 1), dtype=np.complex128)
        self._weights = np.zeros_like(self._history)
        self._previous = np.zeros(frame, dtype=np.float32)
        #: Running estimate of reference power, for the normalisation.
        self._power = np.zeros(self._fft // 2 + 1)
        #: How loud the thing being played was, for the frame just processed.
        #: Read by the barge-in gate: what is left after cancellation has to be
        #: loud enough, relative to what went out, to be a person rather than
        #: the room. Zero when nothing is playing, which is the ordinary state.
        self.reference_rms = 0.0

    # -- the two sides ---------------------------------------------------

    def played(self, block: np.ndarray, *, rate: int = 24000) -> None:
        """Exactly what went to the sound card, as it goes."""
        at_capture = to_capture_rate(block) if rate != self.rate else block
        self._reference = np.concatenate([self._reference, at_capture])

    def silence(self) -> None:
        """Nothing is playing. Clears the alignment rather than the filter --
        the room has not changed, only the signal going into it."""
        self._reference = np.zeros(0, dtype=np.float32)
        self._previous = np.zeros(self.frame, dtype=np.float32)
        self.reference_rms = 0.0

    def process(self, mic: np.ndarray) -> np.ndarray:
        """One capture frame, with the echo estimate subtracted."""
        frame = np.asarray(mic, dtype=np.float32).reshape(-1)
        if frame.size != self.frame:
            return frame

        reference = self._take()
        if reference is None:
            self.reference_rms = 0.0
            return frame
        self.reference_rms = float(np.sqrt(np.mean(np.square(reference))))

        block = np.concatenate([self._previous, reference])
        self._previous = reference
        spectrum = np.fft.rfft(block)

        self._history = np.roll(self._history, 1, axis=0)
        self._history[0] = spectrum

        estimate = np.fft.irfft(np.sum(self._weights * self._history, axis=0))
        error = frame - estimate[self.frame :].astype(np.float32)

        # Overlap-save: only the second half of the block is a valid
        # convolution, so the gradient is computed from an error padded with
        # the zeros the first half must contribute.
        padded = np.concatenate([np.zeros(self.frame, dtype=np.float32), error])
        gradient = np.fft.rfft(padded)

        self._power = 0.9 * self._power + 0.1 * np.abs(spectrum) ** 2
        step = self.mu / (self._power * self.partitions + EPSILON)
        self._weights += step * np.conj(self._history) * gradient

        return error.astype(np.float32)

    # -- alignment -------------------------------------------------------

    def _take(self) -> np.ndarray | None:
        """The reference frame that was in the air when `mic` was captured.

        None when there is not enough of it yet, which is the ordinary state
        whenever Sunday is not speaking -- and returning the microphone
        untouched is exactly right then.
        """
        if self._reference.size < self._delay + self.frame:
            return None
        frame = self._reference[: self.frame].copy()
        self._reference = self._reference[self.frame :]
        return frame


def erle(clean: np.ndarray, cancelled: np.ndarray) -> float:
    """Echo return loss enhancement, in dB. How much quieter it got.

    The number an AEC is judged by. Positive is an improvement; anything above
    about 10 dB on a synthetic path means the filter converged.
    """
    before = float(np.sum(np.square(clean)))
    after = float(np.sum(np.square(cancelled)))
    if after <= 0 or before <= 0:
        return 0.0
    return float(10 * np.log10(before / after))


__all__ = ["Aec", "FRAME", "PARTITIONS", "erle", "to_capture_rate"]

"""The microphone: 16 kHz mono, 20 ms frames, always open.

The stream is opened once and left open. Almost nothing runs on it -- one
5 MB model watches for one phrase -- and that is the point: nothing has to
wake up, so the wake word is instant and barge-in is instant for the same
reason.

`sounddevice` is a thin binding over PortAudio and delivers frames on its own
callback thread. That thread must not block, so it does exactly one thing:
copy the block onto a queue. Everything with a decision in it happens on the
listener thread that drains the queue.

Unplugging a headset mid-session is normal and must not kill the app. The
stream raises, the reader tears it down, re-enumerates, and rebinds to the
configured device or the system default -- reporting an error state for the
second or two that takes.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Iterator

import numpy as np

from sunday import config
from sunday.audio.listener import FRAME_MS

#: How long the reader waits between rebinding attempts after a device error.
RETRY_S = 1.0

#: Frames the queue will hold before it starts dropping the oldest. Two
#: seconds. If the listener is that far behind, the audio is stale anyway and
#: keeping it only makes the backlog worse.
QUEUE_FRAMES = 100


class NoAudio(RuntimeError):
    """`sounddevice` is not installed, or PortAudio found no input."""


def _sounddevice() -> Any:
    try:
        import sounddevice
    except Exception as exc:  # ImportError, or a PortAudio load failure
        raise NoAudio(
            "sounddevice is not available. Install the voice extra: "
            '.venv\\Scripts\\python.exe -m pip install -e ".[voice]"'
        ) from exc
    return sounddevice


def devices() -> list[dict]:
    """Every input device PortAudio can see, for a settings screen or a log."""
    sd = _sounddevice()
    return [dict(d) for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]


class Microphone:
    """One open capture stream, and the frames it produces.

    Iterating yields float32 frames of `frame_samples`. The iterator ends when
    `stop()` is called; a device error does not end it, because a device error
    is something to recover from rather than something to report as the end of
    the audio.
    """

    def __init__(
        self,
        cfg: config.Config | None = None,
        *,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.cfg = cfg or config.get()
        self.rate = self.cfg.audio.sample_rate
        self.frame_samples = round(self.rate * FRAME_MS / 1000)
        self._on_error = on_error
        self._frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=QUEUE_FRAMES)
        self._stop = threading.Event()
        self._stream: Any = None

    # -- the stream -----------------------------------------------------

    def _device(self) -> Any:
        name = self.cfg.audio.input_device.strip()
        return name or None  # None means whatever Windows currently calls default

    def _open(self) -> None:
        sd = _sounddevice()

        def callback(indata, _frames, _time, status) -> None:
            # PortAudio's callback thread. One copy, one put, nothing else --
            # anything slower here shows up as dropped audio, not as lag.
            if status and self._on_error is not None:
                self._on_error(str(status))
            block = np.asarray(indata, dtype=np.float32).reshape(-1).copy()
            try:
                self._frames.put_nowait(block)
            except queue.Full:
                try:
                    self._frames.get_nowait()
                    self._frames.put_nowait(block)
                except queue.Empty:
                    pass

        self._stream = sd.InputStream(
            samplerate=self.rate,
            channels=1,
            dtype="float32",
            blocksize=self.frame_samples,
            device=self._device(),
            callback=callback,
        )
        self._stream.start()

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

    # -- lifecycle ------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()
        self._close()
        self._frames.put(np.zeros(0, dtype=np.float32))  # wake the iterator

    def frames(self) -> Iterator[np.ndarray]:
        """Frames, forever, across device changes."""
        while not self._stop.is_set():
            if self._stream is None:
                try:
                    self._open()
                except NoAudio as exc:
                    # Not a device problem. PortAudio is not installed and is
                    # not going to appear, so retrying is a log line a second
                    # forever -- say it once and stop.
                    if self._on_error is not None:
                        self._on_error(str(exc))
                    return
                except Exception as exc:
                    if self._on_error is not None:
                        self._on_error(f"microphone unavailable: {exc}")
                    if self._stop.wait(RETRY_S):
                        return
                    continue
            try:
                block = self._frames.get(timeout=0.5)
            except queue.Empty:
                if self._stream is not None and not self._alive():
                    self._rebind("the capture stream stopped")
                continue
            if block.size:
                yield block

    def _alive(self) -> bool:
        try:
            return bool(self._stream.active)
        except Exception:
            return False

    def _rebind(self, why: str) -> None:
        if self._on_error is not None:
            self._on_error(f"{why}; rebinding the microphone")
        self._close()


__all__ = ["Microphone", "NoAudio", "devices"]

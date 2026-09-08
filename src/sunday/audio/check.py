r"""Measure the microphone, because the test suite cannot.

    .venv\Scripts\python.exe -m sunday.audio.check

Every number in `[audio]` and `[wake]` is a guess until it is measured on the
hardware it will run on, and the failures they cause all look the same from
the outside: you say the phrase, something says "listening", and then nothing
happens ever again. A quiet microphone, a wake threshold set too high, a VAD
threshold set above your speaking level and a broken model all produce that
exact silence.

So this records a few seconds while you talk and reports what each of the four
pieces made of it, in order, with the number that decided it. It changes
nothing -- it prints what to change.
"""

from __future__ import annotations

import sys
import time

import numpy as np

from sunday import config

#: Long enough to say a wake phrase, pause, and ask a real question.
SECONDS = 8

#: Below this peak, the input is quiet enough that everything downstream is
#: guesswork. Measured against ordinary speech at a normal distance.
QUIET_PEAK = 0.05


def _bar(value: float, scale: float = 0.3, width: int = 28) -> str:
    filled = min(width, int(value / scale * width))
    return "#" * filled + "." * (width - filled)


def _save(audio: np.ndarray, rate: int):
    """Keep the recording. Every question after this one -- was the phrase
    even in there, is the room noisy, did it clip -- is answerable from the
    file and unanswerable from the summary."""
    import wave

    path = config.LOG_DIR / "mic-check.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(audio, -1.0, 1.0) * 32767.0
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.astype(np.int16).tobytes())
    return path


def record(seconds: int = SECONDS) -> np.ndarray:
    from sunday.audio.capture import Microphone

    cfg = config.get()
    mic = Microphone(cfg, on_error=lambda text: print(f"  ! {text}"))
    frames: list[np.ndarray] = []
    wanted = seconds * cfg.audio.sample_rate
    got = 0
    started = time.perf_counter()
    last_line = 0.0

    for frame in mic.frames():
        frames.append(frame)
        got += frame.size
        now = time.perf_counter()
        if now - last_line > 0.2:
            last_line = now
            level = float(np.abs(frame).max())
            left = seconds - int(now - started)
            print(f"\r  {_bar(level)}  peak {level:.4f}   {left:2d}s ", end="", flush=True)
        if got >= wanted:
            break
    mic.stop()
    print()
    return np.concatenate(frames) if frames else np.zeros(0, dtype=np.float32)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass

    cfg = config.get()
    from sunday.audio import models

    outstanding = models.missing(models.required(cfg))
    if outstanding:
        print(f"{len(outstanding)} model(s) not downloaded yet:")
        for key in outstanding:
            print(f"  {key}")
        print(r"  run: .venv\Scripts\python.exe -m sunday.audio.models")
        return 1

    from sunday.audio.capture import devices
    from sunday.audio.stt import Moonshine
    from sunday.audio.vad import WINDOW, Vad
    from sunday.audio.wake import CHUNK, WakeWord

    print("input devices PortAudio can see:")
    for device in devices():
        print(f"  {device['name']}")
    chosen = cfg.audio.input_device or "the system default"
    print(f"config [audio] input_device = {chosen}\n")

    phrase = cfg.wake.model.replace("_", " ")
    print(f'Say "{phrase}", pause, then ask a question. Recording {SECONDS} s.\n')
    audio = record()
    if audio.size == 0:
        print("no audio at all. The stream never opened.")
        return 1

    peak = float(np.abs(audio).max())
    rms = float(np.sqrt(np.mean(np.square(audio))))
    print(f"\n1. capture   {audio.size} samples, peak {peak:.4f}, rms {rms:.4f}")
    if peak < QUIET_PEAK:
        print(
            f"   that is quiet. Windows Settings > System > Sound > Input, raise\n"
            f"   the level; everything below reads as silence until you do."
        )

    detector = WakeWord(cfg.wake.model, threshold=cfg.wake.threshold)
    scored: list[tuple[float, float]] = []
    for start in range(0, audio.size - CHUNK, CHUNK):
        score = detector.feed(audio[start : start + CHUNK])
        if score is not None:
            scored.append((start / cfg.audio.sample_rate, score))

    when, best = max(scored, key=lambda pair: pair[1]) if scored else (0.0, 0.0)
    verdict = "fires" if best >= cfg.wake.threshold else "does NOT fire"
    print(f"2. wake word {verdict}: best {best:.4f} at {when:.1f}s, threshold {cfg.wake.threshold}")

    # The best score on its own says nothing about where the bar belongs. What
    # decides that is the gap between the phrase and everything that is not
    # the phrase -- and a second later in the same recording, you were talking,
    # so "everything else" here is a real negative rather than a quiet room.
    elsewhere = [score for at, score in scored if abs(at - when) > 1.0]
    if elsewhere:
        noise = max(elsewhere)
        print(f"   everything that was not the phrase peaked at {noise:.4f}")
        if best < cfg.wake.threshold and best > noise * 4:
            print(
                f"   the phrase is {best / max(noise, 1e-6):.0f}x above that, so the bar\n"
                f"   is in the wrong place rather than the voice being wrong.\n"
                f"   Try [wake] threshold = {max(0.05, round(best * 0.6, 2))} in config.toml."
            )

    vad = Vad(sample_rate=cfg.audio.sample_rate)
    probabilities = [
        vad.probability(audio[start : start + WINDOW])
        for start in range(0, audio.size - WINDOW, WINDOW)
    ]
    scores = np.array(probabilities) if probabilities else np.zeros(1)
    above = int((scores >= cfg.audio.vad_threshold).sum())
    speech_ms = round(above * WINDOW * 1000 / cfg.audio.sample_rate)
    print(
        f"3. vad       {speech_ms} ms above {cfg.audio.vad_threshold} "
        f"(max {scores.max():.3f}, {above}/{len(scores)} windows)"
    )
    if speech_ms < cfg.audio.min_clip_ms:
        suggestion = max(0.15, round(float(scores.max()) * 0.6, 2))
        print(
            f"   under the {cfg.audio.min_clip_ms} ms floor, so every clip is dropped\n"
            f"   as a cough. Try [audio] vad_threshold = {suggestion} in config.toml."
        )

    # What the pipeline would actually hand over: the speech, plus a margin,
    # not the whole recording. Moonshine returns an empty string for a clip
    # wrapped in silence, so transcribing the raw eight seconds answers a
    # question nothing in the app ever asks.
    from sunday.audio.listener import TRIM_MARGIN_MS

    voiced = np.flatnonzero(scores >= cfg.audio.vad_threshold)
    stt = Moonshine()
    if voiced.size:
        margin = round(TRIM_MARGIN_MS * cfg.audio.sample_rate / 1000)
        start = max(0, int(voiced[0]) * WINDOW - margin)
        end = min(audio.size, (int(voiced[-1]) + 1) * WINDOW + margin)
        clip = audio[start:end]
    else:
        clip = audio

    started = time.perf_counter()
    text = stt.transcribe(clip)
    took = (time.perf_counter() - started) * 1000
    print(f"4. moonshine {took:.0f} ms on {clip.size / cfg.audio.sample_rate:.1f}s -> {text!r}")
    if not text:
        print("   nothing came back. That is the transcriber, not the microphone.")
        whole = stt.transcribe(audio)
        print(f"   the untrimmed {audio.size / cfg.audio.sample_rate:.0f}s gives {whole!r}")

    print(f"\nrecording kept at {_save(audio, cfg.audio.sample_rate)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

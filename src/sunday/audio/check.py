r"""Measure the microphone and the room, because the test suite cannot.

    .venv\Scripts\python.exe -m sunday.audio.check          # the listening half
    .venv\Scripts\python.exe -m sunday.audio.check --echo   # the speaking half

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


def sweep(seconds: float = 0.8, rate: int = 24000) -> np.ndarray:
    """A rising sweep. Broadband, so the cross-correlation has a sharp peak,
    and short, so the reflection of its start is not still arriving at its end."""
    t = np.arange(int(seconds * rate)) / rate
    chirp = np.sin(2 * np.pi * (200 * t + (5000 - 200) / (2 * seconds) * t * t))
    fade = np.minimum(1.0, np.minimum(t, seconds - t) * 40)
    return (chirp * fade * 0.4).astype(np.float32)


def measure_echo() -> int:
    """Play into the room, listen to what comes back, and report the two
    numbers the echo canceller cannot derive for itself."""
    from sunday.audio.aec import FRAME, Aec, erle, to_capture_rate
    from sunday.audio.capture import Microphone, _sounddevice

    cfg = config.get()
    rate = cfg.audio.sample_rate
    sd = _sounddevice()

    tone = sweep()
    lead = np.zeros(int(0.4 * 24000), dtype=np.float32)
    tail = np.zeros(int(0.6 * 24000), dtype=np.float32)
    played = np.concatenate([lead, tone, tail])

    print("Turn the volume to where you normally have it, and stay quiet.")
    print(f"Playing a sweep through {cfg.audio.output_device or 'the default output'}.\n")

    mic = Microphone(cfg, on_error=lambda text: print(f"  ! {text}"))
    frames: list[np.ndarray] = []
    stream = None
    try:
        reader = mic.frames()
        next(reader)  # open the input stream before making any noise
        stream = sd.OutputStream(
            samplerate=24000, channels=1, dtype="float32", blocksize=480,
            device=cfg.audio.output_device.strip() or None,
        )
        stream.start()
        stream.write(played)
        for _ in range(int(0.4 * rate / FRAME)):  # let the tail arrive
            frames.append(next(reader))
    except StopIteration:
        print("the microphone produced nothing at all.")
        return 1
    finally:
        mic.stop()
        if stream is not None:
            stream.stop()
            stream.close()

    heard = np.concatenate(frames) if frames else np.zeros(0, dtype=np.float32)
    reference = to_capture_rate(played)
    if heard.size < reference.size // 2:
        print(f"only {heard.size} samples came back; not enough to measure.")
        return 1

    peak = float(np.abs(heard).max())
    print(f"1. came back  {heard.size} samples, peak {peak:.4f}")
    if peak < 0.01:
        print("   the microphone did not hear the speaker at all. If that is")
        print("   headphones, there is nothing here to measure and nothing to")
        print("   cancel -- which is the good case.")
        return 0

    # Cross-correlate to find the round trip. The peak is where what was
    # played lines up with what came back.
    window = min(heard.size, reference.size)
    correlation = np.correlate(
        heard[:window] - heard[:window].mean(),
        reference[:window] - reference[:window].mean(),
        mode="full",
    )
    lag = int(np.argmax(correlation)) - (window - 1)
    delay_ms = max(0, round(lag * 1000 / rate))
    strength = float(correlation.max() / (np.linalg.norm(heard[:window]) * np.linalg.norm(reference[:window]) + 1e-9))
    print(f"2. round trip {delay_ms} ms (correlation {strength:.3f}), config says {cfg.audio.aec_delay_ms}")
    if strength < 0.05:
        print("   too weak to trust. Raise the volume and run it again.")
        return 1

    # Now run it back through the canceller at the measured delay, and see
    # what is left. That residual is what the barge-in gate has to sit above.
    aec = Aec(frame=FRAME, delay_ms=delay_ms, rate=rate)
    residuals, references = [], []
    for i in range(0, heard.size - FRAME, FRAME):
        aec.played(reference[i : i + FRAME], rate=rate)
        out = aec.process(heard[i : i + FRAME])
        if aec.reference_rms > 0.01:
            residuals.append(float(np.sqrt(np.mean(np.square(out)))))
            references.append(aec.reference_rms)

    if not residuals:
        print("3. cancelled  nothing lined up; the delay is outside what it can hold.")
        return 1

    ratios = np.array(residuals) / np.array(references)
    cancelled = np.array(residuals)
    raw = np.array([
        float(np.sqrt(np.mean(np.square(heard[i : i + FRAME]))))
        for i in range(0, len(residuals) * FRAME, FRAME)
    ])
    print(f"3. cancelled  {erle(raw, cancelled):.1f} dB of echo removed")
    worst = float(np.percentile(ratios, 95))
    print(f"4. residual   {worst:.3f} of what was played, at the 95th percentile")

    suggested = max(0.1, round(worst * 3, 2))
    print("\nMeasured on this machine, for config.toml:")
    print(f"  [audio] aec_delay_ms   = {delay_ms}")
    print(f"  [echo]  barge_in_ratio = {suggested}")
    print("\nThe ratio is what stops it interrupting itself: a person has to be")
    print("that much louder than what is playing before it counts as one.")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass

    cfg = config.get()
    from sunday.audio import models

    outstanding = models.missing(models.required(cfg))
    if outstanding and "--echo" in sys.argv:
        outstanding = [k for k in outstanding if not k.startswith("kokoro/")]
    if outstanding:
        print(f"{len(outstanding)} model(s) not downloaded yet:")
        for key in outstanding:
            print(f"  {key}")
        print(r"  run: .venv\Scripts\python.exe -m sunday.audio.models")
        return 1

    if "--echo" in sys.argv:
        return measure_echo()

    if "--voices" in sys.argv:
        from sunday.audio.tts import Speech

        for name in Speech(cfg).voices():
            print(f"  {name}")
        print(f'\nconfig: [tts] voice = "{cfg.tts.voice}"')
        return 0

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

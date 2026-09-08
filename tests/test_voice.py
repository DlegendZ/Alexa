"""Voice in: the listener's rules, and the models when they are on disk.

Almost all of this runs with no sound card, no ONNX runtime and no models,
because `Listener` takes its wake word and its VAD as objects and counts time
in samples rather than off a clock. The tests at the bottom are the opposite
-- they load the real models -- and they skip when the download has not been
done.
"""

from __future__ import annotations

import numpy as np
import pytest

from sunday import config
from sunday.audio import models
from sunday.audio.listener import FRAME_MS, TRIM_MARGIN_MS, Listener

FRAME = 320  # 20 ms at 16 kHz
LOUD = 0.2
QUIET = 0.0


class FakeWake:
    """Fires on the nth 80 ms chunk it is given. Ten chunks is 40 frames,
    which is long enough for the pre-roll ring to have filled."""

    def __init__(self, at: int = 10, score: float = 0.99) -> None:
        self.at = at
        self.score = score
        self.always = False
        self.chunks = 0
        self.resets = 0

    def feed(self, _chunk) -> float | None:
        self.chunks += 1
        if self.always or self.chunks == self.at:
            return self.score
        return 0.0

    def reset(self) -> None:
        self.resets += 1
        self.chunks = 0


class LevelVad:
    """Speech is whatever is loud.

    Reacting to the audio rather than counting calls is what lets a test say
    "forty frames of speech, then forty of silence" and have that stay true
    once the pre-roll started going through the VAD as well.
    """

    def __init__(self, floor: float = 0.1) -> None:
        self.floor = floor
        self.windows = 0
        self.resets = 0

    def probability(self, window) -> float:
        self.windows += 1
        return 1.0 if float(np.abs(window).mean()) >= self.floor else 0.0

    def reset(self) -> None:
        self.resets += 1
        self.windows = 0


def tone(frames: int = 1, level: float = LOUD) -> np.ndarray:
    return np.full(FRAME * frames, level, dtype=np.float32)


def drive(listener: Listener, frames: int, level: float = LOUD) -> list:
    out = []
    for _ in range(frames):
        out.extend(listener.frame(tone(level=level)))
    return out


def kinds(events, *, drop: str = "level") -> list[str]:
    return [e.kind for e in events if e.kind != drop]


def utterance(
    listener: Listener, *, before: int, speech: int, after: int, lead: float = QUIET
) -> list:
    """Fill the ring, fire the wake word, say something, stop talking."""
    events = drive(listener, before, lead)
    events += drive(listener, speech, LOUD)
    events += drive(listener, after, QUIET)
    return events


# -- the state machine ----------------------------------------------------


def test_wake_opens_a_clip_that_carries_the_preroll(cfg):
    """People start the question before they finish the trigger, so the half
    second before the wake word fired has to be in the clip."""
    listener = Listener(cfg, wake=FakeWake(), vad=LevelVad())

    # Forty frames of speech: the ring is full and loud when the wake fires.
    events = drive(listener, 40, LOUD)
    assert "wake" in kinds(events)
    assert listener.phase == "recording"

    events = drive(listener, 50, LOUD) + drive(listener, 60, QUIET)
    clips = [e for e in events if e.kind == "clip"]
    assert len(clips) == 1
    clip = clips[0].clip

    # 500 ms of pre-roll on top of the 1000 ms said after the wake fired.
    assert clip.speech_ms >= cfg.audio.preroll_ms + 900
    assert clip.forced is False
    # The audio handed over is the whole clip, not a summary of it.
    assert round(clip.audio.size * 1000 / cfg.audio.sample_rate) == clip.total_ms


def test_the_clip_is_trimmed_to_the_speech(cfg):
    """Moonshine answers a clip wrapped in silence by emitting end-of-sequence
    as its very first token -- an empty transcript for audio with words plainly
    in it. Measured on this build: two seconds of padding either side of four
    seconds of clear speech is enough to do it.

    Every clip here carries pre-roll in front and 700 ms of trailing silence
    behind, by construction. Without the trim the common case is the failing
    case, which is exactly how it was found.
    """
    listener = Listener(cfg, wake=FakeWake(), vad=LevelVad())
    events = utterance(listener, before=40, speech=25, after=60)

    clip = [e for e in events if e.kind == "clip"][0].clip
    recorded = (40 + 25 + 60) * FRAME_MS
    margin = 2 * TRIM_MARGIN_MS

    assert clip.total_ms < recorded
    assert clip.speech_ms <= clip.total_ms <= clip.speech_ms + margin + FRAME_MS


def test_the_pause_after_the_phrase_does_not_end_the_clip(cfg):
    """Starting and stopping are two different clocks.

    `vad_silence_ms` is 700 ms and the pause between "hey jarvis" and the
    question is routinely twice that -- measured at 1.4 s on the recording
    that found this. The pre-roll holds the wake phrase, so counting it as
    "speech has begun" starts the stopwatch that ends the clip, and the clip
    closes inside the pause with only the phrase in it. That got dropped as a
    cough, and from outside it was indistinguishable from a dead microphone.
    """
    listener = Listener(cfg, wake=FakeWake(), vad=LevelVad())

    # The wake phrase, loud, filling the ring. The wake fires at the end of it.
    drive(listener, 40, LOUD)
    assert listener.phase == "recording"

    # Two seconds of thinking about it -- nearly three times the silence that
    # ends a clip, and well under the lead-in that abandons one.
    events = drive(listener, 100, QUIET)
    assert kinds(events) == []
    assert listener.phase == "recording"

    events = drive(listener, 60, LOUD) + drive(listener, 60, QUIET)
    clips = [e for e in events if e.kind == "clip"]
    assert clips, kinds(events)
    # The question is in there, not just the phrase that woke it.
    assert clips[0].clip.speech_ms >= 60 * FRAME_MS


def test_a_phrase_with_no_question_after_it_is_still_abandoned(cfg):
    """The other half of the same rule. Waiting longer for the question must
    not turn into waiting forever for one that never comes."""
    cfg.audio.lead_in_ms = 600
    listener = Listener(cfg, wake=FakeWake(), vad=LevelVad())
    drive(listener, 40, LOUD)  # the phrase, and nothing after it
    events = drive(listener, 60, QUIET)
    dropped = [e for e in events if e.kind == "dropped"]
    assert dropped and dropped[0].why == "nothing was said"
    assert listener.phase == "sleeping"


def test_the_clip_closes_on_silence_not_before(cfg):
    listener = Listener(cfg, wake=FakeWake(), vad=LevelVad())
    drive(listener, 40, QUIET)
    assert listener.phase == "recording"
    drive(listener, 30, LOUD)

    # One frame at a time, so the answer is *when*, not merely whether.
    quiet = 0
    while listener.phase == "recording" and quiet < 200:
        quiet += 1
        if any(e.kind == "clip" for e in listener.frame(tone(level=QUIET))):
            break
    heard = quiet * FRAME_MS
    assert cfg.audio.vad_silence_ms <= heard <= cfg.audio.vad_silence_ms + 120


def test_a_cough_never_becomes_a_clip(cfg):
    """`min_clip_ms` is measured against the speech, not against the clip --
    every clip carries pre-roll and trailing silence, so a guard on the total
    could never fire at all.

    It is now one number doing one job at both ends: below the floor is not
    enough speech to have begun talking, and it is not enough speech to have
    said anything. So a cough is abandoned on the lead-in rather than closed
    and then thrown away, and either way nothing reaches the transcriber.
    """
    cfg.audio.lead_in_ms = 800
    listener = Listener(cfg, wake=FakeWake(), vad=LevelVad())
    # Eight frames is 160 ms of speech, well under the 300 ms floor.
    events = utterance(listener, before=40, speech=8, after=80)
    assert not [e for e in events if e.kind == "clip"]
    assert [e.why for e in events if e.kind == "dropped"] == ["nothing was said"]


def test_a_long_clip_is_force_closed_and_kept(cfg):
    cfg.audio.max_clip_ms = 1000
    listener = Listener(cfg, wake=FakeWake(), vad=LevelVad())
    drive(listener, 40, QUIET)
    events = drive(listener, 200, LOUD)
    clips = [e for e in events if e.kind == "clip"]
    assert clips and clips[0].clip.forced is True


def test_woken_by_the_television_gives_up(cfg):
    """A wake word that fires with nothing said after it must not record
    silence until the thirty-second cap."""
    cfg.audio.lead_in_ms = 500
    listener = Listener(cfg, wake=FakeWake(), vad=LevelVad())
    drive(listener, 40, QUIET)
    events = drive(listener, 60, QUIET)
    dropped = [e for e in events if e.kind == "dropped"]
    assert dropped and dropped[0].why == "nothing was said"
    assert listener.phase == "sleeping"


def test_one_phrase_cannot_open_two_turns(cfg):
    """At 80 ms a step a spoken phrase scores above threshold for a dozen
    consecutive steps. The cooldown is what stops that being a dozen turns."""
    wake = FakeWake()
    listener = Listener(cfg, wake=wake, vad=LevelVad())
    drive(listener, 40, QUIET)
    assert wake.resets == 1  # the phrase is out of the detector's own window

    listener.abandon()
    wake.always = True  # every chunk from here scores 0.99

    # 1500 ms of cooldown is 24000 samples; 20 frames is 6400 of them.
    assert "wake" not in kinds(drive(listener, 20, QUIET))
    assert "wake" in kinds(drive(listener, 80, QUIET))


def test_muted_decides_nothing(cfg):
    wake = FakeWake(at=1)
    listener = Listener(cfg, wake=wake, vad=LevelVad())
    listener.muted = True
    assert drive(listener, 100) == []
    assert wake.chunks == 0


def test_level_is_throttled_and_real(cfg):
    listener = Listener(cfg, wake=None, vad=None)
    events = [e for e in drive(listener, 50, level=0.5) if e.kind == "level"]
    # One every 100 ms, not one every frame.
    assert 8 <= len(events) <= 12
    assert all(abs(e.rms - 0.5) < 0.01 for e in events)


def test_speaking_raises_the_bar(cfg):
    """Echo control layer 2. Residual echo is quiet and choppy; a person
    interrupting is loud and continuous."""
    listener = Listener(cfg, wake=None, vad=None)
    assert listener._threshold() == cfg.audio.vad_threshold
    listener.speaking = True
    assert listener._threshold() == cfg.echo.speech_threshold_while_speaking
    assert listener._threshold() > cfg.audio.vad_threshold


def test_push_to_talk_needs_no_phrase(cfg):
    listener = Listener(cfg, wake=None, vad=LevelVad())
    drive(listener, 40, QUIET)
    assert listener.phase == "sleeping"
    listener.open()
    assert listener.phase == "recording"
    events = drive(listener, 40, LOUD) + drive(listener, 60, QUIET)
    assert "clip" in kinds(events)


def test_abandon_throws_the_clip_away(cfg):
    listener = Listener(cfg, wake=FakeWake(), vad=LevelVad())
    drive(listener, 40, LOUD)
    assert listener.phase == "recording"
    listener.abandon()
    assert listener.phase == "sleeping"
    assert not [e for e in drive(listener, 60, QUIET) if e.kind == "clip"]


# -- the model files ------------------------------------------------------


def test_a_missing_model_says_what_to_run(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MODEL_DIR", tmp_path)
    with pytest.raises(models.ModelMissing) as caught:
        models.model_path("vad/silero_vad.onnx")
    message = str(caught.value)
    assert "vad/silero_vad.onnx" in message
    assert "sunday.audio.models" in message


def test_every_required_model_has_somewhere_to_come_from():
    """A key that `required()` asks for and `ASSETS` has never heard of is a
    download that fails at the last possible moment."""
    for key in models.required(config.Config()):
        assert key in models.ASSETS, key


def test_the_wake_phrase_decides_the_file():
    assert models.wake_key("hey_jarvis") == "wake/hey_jarvis.onnx"
    assert models.wake_key("Hey Mycroft") == "wake/hey_mycroft.onnx"


# -- the real thing -------------------------------------------------------

try:  # the models are a quarter of a gigabyte and are not in the repo
    _missing = models.missing(models.required(config.Config()))
except Exception:  # pragma: no cover - config unreadable
    _missing = ["config"]

needs_models = pytest.mark.skipif(
    bool(_missing), reason=f"voice models not downloaded: {_missing}"
)


@needs_models
def test_silero_hears_speech_and_not_silence():
    """The 64 samples of context Silero v5 wants are easy to leave out, and
    leaving them out does not raise -- it returns 0.02 for clear speech."""
    from sunday.audio.vad import WINDOW, Vad

    vad = Vad()
    silence = [vad.probability(np.zeros(WINDOW, dtype=np.float32)) for _ in range(10)]
    assert max(silence) < 0.3

    rng = np.random.default_rng(0)
    # A 140 Hz sawtooth is not speech, but it is not silence either, and the
    # point here is that the graph responds to anything at all.
    t = np.arange(WINDOW * 20) / 16000.0
    buzz = (np.mod(t * 140.0, 1.0) - 0.5).astype(np.float32) * 0.5
    buzz += rng.normal(0, 0.01, buzz.shape).astype(np.float32)
    heard = [
        vad.probability(buzz[i : i + WINDOW])
        for i in range(0, len(buzz) - WINDOW, WINDOW)
    ]
    assert max(heard) > max(silence)


@needs_models
def test_moonshine_returns_nothing_for_a_clip_too_short_to_hold_a_word():
    from sunday.audio.stt import MIN_SAMPLES, Moonshine

    assert Moonshine().transcribe(np.zeros(MIN_SAMPLES - 1, dtype=np.float32)) == ""


@needs_models
def test_the_wake_word_does_not_fire_on_silence():
    from sunday.audio.wake import CHUNK, WakeWord

    detector = WakeWord("hey_jarvis", threshold=0.5)
    best = 0.0
    for _ in range(40):
        score = detector.feed(np.zeros(CHUNK, dtype=np.float32))
        if score is not None:
            best = max(best, score)
    assert best < 0.5


# -- which transcriber ----------------------------------------------------


def test_the_config_decides_which_transcriber_is_built(cfg):
    """Two are kept because the measurement did not favour the default one,
    and a switch you cannot flip is not a record of that."""
    from sunday.audio import stt

    cfg.models.stt = "moonshine-base"
    assert stt.load.__module__ == "sunday.audio.stt"
    assert models.transcriber(cfg) == models.MOONSHINE

    cfg.models.stt = "parakeet-tdt-0.6b-v2"
    assert models.transcriber(cfg) == models.PARAKEET


def test_only_the_chosen_transcriber_is_downloaded(cfg):
    """Fetching both is 900 MB to use one of them."""
    cfg.models.stt = "parakeet-tdt-0.6b-v2"
    keys = models.required(cfg)
    assert set(models.PARAKEET) <= set(keys)
    assert not set(models.MOONSHINE) & set(keys)

    cfg.models.stt = "moonshine-base"
    keys = models.required(cfg)
    assert set(models.MOONSHINE) <= set(keys)
    assert not set(models.PARAKEET) & set(keys)


def test_the_parakeet_encoder_is_first_in_its_list():
    """`onnx-asr` is handed the directory rather than the files, and the
    encoder's key is how that directory is worked out."""
    assert models.PARAKEET[0] == "parakeet/encoder-model.int8.onnx"
    assert all(k.startswith("parakeet/") for k in models.PARAKEET)


@needs_models
def test_the_configured_transcriber_refuses_a_clip_with_no_word_in_it():
    """Parakeet invents a filler word for a clip of 100 ms. At any length the
    listener would actually send, both models return nothing for silence --
    but the floor is what stops the short case ever arriving."""
    from sunday.audio import stt

    transcriber = stt.load(config.get())
    assert transcriber.transcribe(np.zeros(stt.MIN_SAMPLES - 1, dtype=np.float32)) == ""
    assert transcriber.transcribe(np.zeros(48000, dtype=np.float32)) == ""

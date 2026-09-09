"""Voice out and echo control: the mouth, the three layers, and barge-in.

The speaker is driven with a fake synthesiser and a fake sound card, because
what it has to get right is not audio -- it is which turn a sentence belongs
to, and how fast it stops. The echo canceller is driven with a synthetic room,
which is the only way to know a number like ERLE at all. Layer 3 is a set
comparison and needs nothing.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from sunday.audio import echo
from sunday.audio.aec import FRAME, Aec, erle, to_capture_rate
from sunday.audio.listener import FRAME_MS, Listener
from sunday.audio.speaker import BLOCK, Speaker
from tests.test_voice import (
    FakeWake, LevelVad, LOUD, QUIET, drive, kinds, utterance,
)


class FakeSpeech:
    """A synthesiser that costs nothing and says how long it was asked for."""

    def __init__(self, seconds: float = 0.5) -> None:
        self.seconds = seconds
        self.asked: list[str] = []

    def say(self, sentence: str):
        self.asked.append(sentence)
        return np.full(int(24000 * self.seconds), 0.1, dtype=np.float32)


class FakeStream:
    """A sound card that takes as long as real time to accept a block."""

    def __init__(self, per_block_s: float = 0.0) -> None:
        self.per_block_s = per_block_s
        self.written = 0
        self.blocks: list[np.ndarray] = []

    def write(self, block) -> None:
        self.written += len(block)
        self.blocks.append(np.asarray(block).copy())
        if self.per_block_s:
            time.sleep(self.per_block_s)


def wired(cfg, *, speech=None, stream=None, **kwargs) -> Speaker:
    speaker = Speaker(cfg, speech=speech or FakeSpeech(), **kwargs)
    speaker._stream = stream or FakeStream()
    speaker.start()
    return speaker


def settle(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


# -- the mouth ------------------------------------------------------------


def test_sentences_are_spoken_in_order(cfg):
    speech = FakeSpeech(seconds=0.05)
    stream = FakeStream()
    speaker = wired(cfg, speech=speech, stream=stream)
    try:
        for sentence in ("One.", "Two.", "Three."):
            speaker.say(sentence)
        assert settle(lambda: speech.asked == ["One.", "Two.", "Three."])
        assert settle(lambda: not speaker.speaking)
    finally:
        speaker.close()


def test_nothing_is_synthesised_for_punctuation(cfg):
    """The splitter sends whatever is left when the stream ends, and a reply
    that stops mid-token leaves a stray mark behind."""
    speech = FakeSpeech()
    speaker = wired(cfg, speech=speech)
    try:
        speaker.say("   ")
        speaker.say("Real words here.")
        assert settle(lambda: speech.asked == ["Real words here."])
    finally:
        speaker.close()


def test_stopping_drops_the_queue_and_the_sentence_in_flight(cfg):
    """Barge-in targets under 100 ms from the first syllable to silence, and
    most of a naive implementation's delay is audio already handed over."""
    speech = FakeSpeech(seconds=2.0)  # 96 blocks
    stream = FakeStream(per_block_s=0.002)
    speaker = wired(cfg, speech=speech, stream=stream)
    try:
        for sentence in ("A long first sentence.", "And a second.", "And a third."):
            speaker.say(sentence)
        assert settle(lambda: stream.written > BLOCK * 3)

        written = stream.written
        speaker.stop()
        assert settle(lambda: not speaker.speaking)
        # One block may still be in flight; a whole sentence may not.
        assert stream.written - written <= BLOCK * 2
        # Sentences two and three were synthesised ahead -- that is the point
        # of the split -- but not a sample of either reached the sound card.
        assert stream.written < 24000 * 2.0
    finally:
        speaker.close()


def test_a_stop_does_not_silence_the_next_turn(cfg):
    """Which turn a sentence belongs to is a number, not a flag. A latched
    flag either stops the turn after it as well, or has to be cleared by
    whoever speaks next -- and both of those are races."""
    speech = FakeSpeech(seconds=0.05)
    speaker = wired(cfg, speech=speech)
    try:
        speaker.say("Interrupted.")
        speaker.stop()
        speaker.say("The next turn.")
        assert settle(lambda: "The next turn." in speech.asked)
    finally:
        speaker.close()


def test_what_was_played_is_reported_exactly(cfg):
    """Layer 1's whole advantage is that the reference is not a guess."""
    played: list[np.ndarray] = []
    speech = FakeSpeech(seconds=0.1)
    stream = FakeStream()
    speaker = wired(cfg, speech=speech, stream=stream, on_played=played.append)
    try:
        speaker.say("Say something.")
        assert settle(lambda: speech.asked and not speaker.speaking)
        assert np.array_equal(np.concatenate(played), np.concatenate(stream.blocks))
    finally:
        speaker.close()


def test_the_speaking_flag_survives_the_gap_between_sentences(cfg):
    """A gap between two sentences is not the end of the reply. Reporting it
    as one drops the VAD bar in the middle of Sunday talking, which is the
    hole layer 2 exists to close."""
    seen: list[str] = []
    speech = FakeSpeech(seconds=0.02)
    speaker = wired(cfg, speech=speech, on_state=lambda state, _: seen.append(state))
    try:
        speaker.say("One.")
        speaker.say("Two.")
        speaker.say("Three.")
        assert settle(lambda: speech.asked.count("Three.") == 1)
        assert settle(lambda: seen and seen[-1] == "idle")
        # Three sentences, and the orb goes back to idle once.
        assert seen.count("idle") == 1, seen
    finally:
        speaker.close()


# -- layer 1 --------------------------------------------------------------


def test_the_canceller_converges_on_a_synthetic_room():
    rng = np.random.default_rng(0)
    rate, seconds = 16000, 6
    n = rate * seconds

    far = rng.normal(0, 0.3, n).astype(np.float32)
    smooth = np.hanning(64) / np.hanning(64).sum()
    far = np.convolve(far, smooth, mode="same").astype(np.float32)

    path = np.zeros(400, dtype=np.float32)
    path[40], path[95], path[180], path[330] = 0.6, -0.25, 0.12, -0.05
    mic = np.convolve(far, path)[:n].astype(np.float32)
    mic += rng.normal(0, 0.0005, n).astype(np.float32)

    aec = Aec(delay_ms=0, rate=rate)
    out = []
    for i in range(0, n - FRAME, FRAME):
        aec.played(far[i : i + FRAME], rate=rate)
        out.append(aec.process(mic[i : i + FRAME]))
    cancelled = np.concatenate(out)

    half = cancelled.size // 2
    assert erle(mic[half : cancelled.size], cancelled[half:]) > 15


def test_the_microphone_is_untouched_when_nothing_is_playing():
    """The ordinary state. A canceller that colours the input when there is no
    reference is a canceller that quietly breaks the VAD."""
    aec = Aec(delay_ms=0)
    frame = np.linspace(-0.5, 0.5, FRAME, dtype=np.float32)
    assert np.array_equal(aec.process(frame), frame)


def test_the_reference_is_resampled_exactly():
    """24 kHz in, 16 kHz out. Two up, three down -- not an interpolation."""
    t = np.arange(24000) / 24000.0
    tone = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    down = to_capture_rate(tone)
    assert down.size == 16000

    spectrum = np.abs(np.fft.rfft(down * np.hanning(down.size)))
    peak = int(np.argmax(spectrum)) * 16000 / down.size
    assert abs(peak - 440) < 2


# -- layer 2 --------------------------------------------------------------


def test_barge_in_needs_a_burst_not_a_frame(cfg):
    """Residual echo is quiet and choppy; a person interrupting is loud and
    continuous. One window of either is not a decision."""
    listener = Listener(cfg, wake=FakeWake(), vad=LevelVad())
    listener.speaking = True

    # One frame of speech is 320 samples: not even a whole VAD window.
    assert "barge_in" not in kinds(drive(listener, 1, LOUD))

    events = drive(listener, 20, LOUD)
    assert "barge_in" in kinds(events)
    # And it opens a clip, so what you said becomes the next turn.
    assert listener.phase == "recording"


def test_a_choppy_burst_is_not_an_interruption(cfg):
    listener = Listener(cfg, wake=FakeWake(), vad=LevelVad())
    listener.speaking = True
    for _ in range(20):
        drive(listener, 2, LOUD)
        drive(listener, 3, QUIET)
    assert listener.phase == "sleeping"


def test_the_wake_word_is_not_consulted_while_it_is_talking(cfg):
    """You are already talking to it. Requiring the phrase to interrupt is
    requiring you to be polite to something that will not stop."""
    wake = FakeWake()
    wake.always = True
    listener = Listener(cfg, wake=wake, vad=LevelVad())
    listener.speaking = True
    drive(listener, 20, QUIET)
    assert wake.chunks == 0
    assert listener.phase == "sleeping"


# -- layer 3 --------------------------------------------------------------


@pytest.mark.parametrize(
    "heard",
    [
        "gold is at 2412.55",  # the comma did not survive the round trip
        "Gold is at 2,412.55.",
        "at 2412.55",  # only part of the sentence reached the microphone
    ],
)
def test_its_own_voice_coming_back_is_recognised(heard):
    assert echo.is_echo(heard, "Gold is at 2,412.55.")


def test_a_number_read_aloud_is_the_one_thing_it_cannot_see():
    """Stated rather than discovered later. Token overlap cannot connect
    "2412.55" to "twenty four twelve", so a reply that is mostly a number can
    get through layer 3. Layers 1 and 2 are what stand behind it."""
    assert not echo.is_echo("gold is at twenty four twelve", "Gold is at 2,412.55.")


@pytest.mark.parametrize(
    "heard",
    [
        "what is the weather in Jakarta",
        "no, the other one",
        "stop",
        "and what about silver",
    ],
)
def test_a_person_is_not_mistaken_for_the_echo(heard):
    assert not echo.is_echo(heard, "Gold is at 2,412.55.")


def test_silence_is_never_an_echo():
    """Nothing is being spoken, so nothing can be coming back."""
    assert not echo.is_echo("anything at all", "")


def test_the_filler_words_carry_no_evidence():
    """A reply and an unrelated question share 'the' and 'is' for free, and on
    short sentences that alone clears the cutoff."""
    assert not echo.is_echo("is it the one", "The weather is clear and it is warm.")
    assert echo.words("The gold is in it") == {"gold"}


# -- the follow-up window -------------------------------------------------


def test_the_second_question_needs_no_wake_word(cfg):
    """Saying the phrase before every question turns a conversation into a
    sequence of summonings. After a reply the door stays open for a while, and
    speech alone opens the next clip."""
    listener = Listener(cfg, wake=FakeWake(at=9999), vad=LevelVad())
    drive(listener, 40, QUIET)
    assert listener.phase == "sleeping"  # no phrase, no wake

    listener.expect_follow_up()
    events = drive(listener, 20, LOUD)
    assert "follow_up" in kinds(events)
    assert listener.phase == "recording"


def test_the_window_closes_and_the_phrase_is_needed_again(cfg):
    cfg.wake.follow_up_ms = 200
    listener = Listener(cfg, wake=FakeWake(at=9999), vad=LevelVad())
    listener.expect_follow_up()

    # Quiet for longer than the window, then talk.
    drive(listener, 20, QUIET)
    events = drive(listener, 20, LOUD)
    assert "follow_up" not in kinds(events)
    assert listener.phase == "sleeping"


def test_a_quiet_room_does_not_open_the_window_by_itself(cfg):
    listener = Listener(cfg, wake=FakeWake(at=9999), vad=LevelVad())
    listener.expect_follow_up()
    assert kinds(drive(listener, 100, QUIET)) == []
    assert listener.phase == "sleeping"


def test_the_window_starts_when_the_reply_ends_not_when_it_was_written(cfg):
    """Most of an eight-second window spent listening to the speaker is not an
    eight-second window."""
    listener = Listener(cfg, wake=FakeWake(at=9999), vad=LevelVad())
    listener.speaking = True
    listener.expect_follow_up()
    # While speaking, the same sustained speech is barge-in, not follow-up.
    events = drive(listener, 20, LOUD)
    assert kinds(events) == ["barge_in"]


# -- the gate that stops it interrupting itself ---------------------------


def test_its_own_voice_does_not_count_as_an_interruption(cfg):
    """The bug this exists for: it heard itself and cut itself off.

    The VAD cannot help here and no threshold on it can. Sunday's voice
    returning through the room *is* speech, and Silero scores it 1.0 -- so
    layer 2's raised bar separates loud speech from quiet speech, and both
    sides of this are loud speech. What separates them is that the residual
    left after cancellation is small against what was played, and a person is
    not.
    """
    listener = Listener(cfg, wake=FakeWake(at=9999), vad=LevelVad(floor=0.01))
    listener.speaking = True
    # Playing at 0.4; a well-cancelled echo leaves a few percent of that.
    listener.reference_rms = 0.4
    events = drive(listener, 40, 0.03)
    assert kinds(events) == []
    assert listener.phase == "sleeping"


def test_a_person_over_the_top_of_it_still_gets_through(cfg):
    """The other half. A gate that never opens is not a fix."""
    listener = Listener(cfg, wake=FakeWake(at=9999), vad=LevelVad(floor=0.01))
    listener.speaking = True
    listener.reference_rms = 0.4
    events = drive(listener, 20, 0.25)
    assert "barge_in" in kinds(events)


def test_the_gate_is_relative_not_absolute(cfg):
    """Quiet playback means a quiet interruption still counts. An absolute
    floor would make barge-in depend on the volume knob."""
    listener = Listener(cfg, wake=FakeWake(at=9999), vad=LevelVad(floor=0.001))
    listener.speaking = True
    listener.reference_rms = 0.02  # playing quietly
    events = drive(listener, 20, 0.02)
    assert "barge_in" in kinds(events)


def test_nothing_playing_means_nothing_to_be_louder_than(cfg):
    """The follow-up window runs with the speaker silent, so the gate must be
    open by default rather than closed."""
    listener = Listener(cfg, wake=FakeWake(at=9999), vad=LevelVad())
    listener.expect_follow_up()
    assert listener.reference_rms == 0.0
    assert "follow_up" in kinds(drive(listener, 20, LOUD))


# -- the transcript check only looks where an echo could be ---------------


def test_asking_about_the_same_thing_twice_is_not_an_echo():
    """The bug that made it unusable, and no threshold could have fixed it.

    A reply always contains its question's subject, so the *next* question
    about that subject overlaps the answer. Measured on real wording: "what is
    the weather in Jakarta" scores 0.67 against the reply to it, and the
    follow-up "and the humidity" scores 1.00, because every word of it is in
    the answer. Both were being thrown away, and the person was told their own
    voice sounded like Sunday's.
    """
    reply = "The weather in Jakarta is 26.0 degrees Celsius with a clear sky, 77% humidity."
    assert echo.similarity("what is the weather in Jakarta", reply) >= 0.6
    assert echo.similarity("and the humidity", reply) >= 0.6


def test_a_clip_recorded_in_silence_is_never_checked_for_echo(cfg):
    """Which is what makes the case above harmless. The spec says to compare
    against the sentence Kokoro is *currently speaking*; a clip recorded while
    nothing was playing cannot hold Sunday's voice, whatever it sounds like."""
    listener = Listener(cfg, wake=FakeWake(), vad=LevelVad())
    events = utterance(listener, before=40, speech=30, after=60)
    clip = [e for e in events if e.kind == "clip"][0].clip
    assert clip.while_speaking is False


def test_a_clip_that_overlapped_the_reply_is_marked(cfg):
    """And the other half: an echo has to stay catchable."""
    listener = Listener(cfg, wake=FakeWake(), vad=LevelVad())
    drive(listener, 40, QUIET)
    assert listener.phase == "recording"

    listener.speaking = True  # Kokoro starts mid-clip
    drive(listener, 30, LOUD)
    listener.speaking = False
    events = drive(listener, 60, QUIET)

    clip = [e for e in events if e.kind == "clip"][0].clip
    assert clip.while_speaking is True


def test_the_barge_in_floor_survives_the_gap_between_sentences():
    """`speaking` stays true between two sentences of one reply, but the
    speaker stops producing reference while the next is synthesised. Zeroing
    the floor there opens the gate at the one moment residual echo is loudest.
    `silence()` is what says the reply has ended -- not a quiet moment in it."""
    aec = Aec(delay_ms=0)
    played = np.full(FRAME * 4, 0.3, dtype=np.float32)
    aec.played(played, rate=16000)
    for _ in range(4):
        aec.process(np.zeros(FRAME, dtype=np.float32))
    assert aec.reference_rms > 0.2

    # The gap: nothing left to align against, but the reply is not over.
    aec.process(np.zeros(FRAME, dtype=np.float32))
    assert aec.reference_rms > 0.2

    aec.silence()
    assert aec.reference_rms == 0.0


def test_the_window_lasts_as_long_as_it_says(cfg):
    """It was eight seconds and is now thirty, which is only a safe change if
    the number means what it claims. Drive silence to just inside the window,
    then just past it, and check the door is open for one and shut for the
    other."""
    cfg.wake.follow_up_ms = 1000
    frames_inside = round(cfg.wake.follow_up_ms / FRAME_MS) - 5
    frames_past = round(cfg.wake.follow_up_ms / FRAME_MS) + 5

    listener = Listener(cfg, wake=FakeWake(at=9999), vad=LevelVad())
    listener.expect_follow_up()
    drive(listener, frames_inside, QUIET)
    assert "follow_up" in kinds(drive(listener, 20, LOUD))

    listener = Listener(cfg, wake=FakeWake(at=9999), vad=LevelVad())
    listener.expect_follow_up()
    drive(listener, frames_past, QUIET)
    assert "follow_up" not in kinds(drive(listener, 20, LOUD))
    assert listener.phase == "sleeping"

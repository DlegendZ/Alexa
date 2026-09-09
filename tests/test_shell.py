"""The shell's half of the contract.

The orb is the only status display the app has, so a state the sidecar emits
and the orb cannot draw is a turn that happens in silence, and an orb state
nothing emits is a look nobody ever sees. Both have happened: `wake` was in the
state table from the first draft and had no producer for three milestones, the
same shape of bug as a trace line that is written and never called.

So the two sets are compared here rather than trusted. The tests read the
JavaScript, because the orb is where the drawing lives and duplicating its list
in Python would only prove the duplicate was in step with itself.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ORB = REPO / "app" / "src" / "lib" / "orb.js"
PACKAGE = REPO / "src" / "sunday"

#: States the shell owns and the sidecar never sends. The window is in this one
#: before the socket has said anything at all, which is a fact about the shell
#: and not about the turn.
SHELL_ONLY = {"connecting"}

#: States the client derives from a message that is not a `state`. The sidecar
#: says `tool` with a scope and `blocked` with a name; the orb turns those into
#: a colour, because the alternative is a second producer for the same fact.
DERIVED = {"tool.local", "tool.external", "blocked"}


def orb_states() -> set[str]:
    """Both halves: the resting looks, and the ones that are a flash over
    whatever is happening. A state the orb answers by flashing is a state the
    orb knows -- reading only the looks would have called `wake` unhandled."""
    source = ORB.read_text(encoding="utf-8")
    found: set[str] = set()
    for name, pattern in (
        ("LOOKS", r"const LOOKS = \{(.*?)\n\};"),
        ("FLASHES", r"const FLASHES = \{(.*?)\n\};"),
    ):
        block = re.search(pattern, source, re.S)
        assert block, f"orb.js no longer declares {name} the way this test reads it"
        # Keys sit at the start of a line; a look's own fields do not.
        found.update(re.findall(r"^\s*'?([a-z.]+)'?:", block.group(1), re.M))
    return found


def emitted_states() -> set[str]:
    """Every `state` value the package can put on the socket.

    Read off the source rather than by running a turn: half of these need a
    microphone, and the point is to enumerate the paths, not the ones that
    happened to run.

    Three spellings, because the package has three: a dict literal on the
    socket, the runtime's keyword form, and the ear's own helper. Reading one
    of them is how `thinking` looked unproduced while being emitted on every
    turn that thinks.
    """
    found: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        found.update(re.findall(r'"value": "([a-z.]+)"', source))
        found.update(re.findall(r'value="([a-z.]+)"', source))
        found.update(re.findall(r'_say\(state="([a-z.]+)"\)', source))
    return found


def test_the_orb_can_draw_every_state_the_sidecar_sends():
    undrawable = emitted_states() - orb_states()
    assert not undrawable, (
        f"the sidecar emits {sorted(undrawable)} and the orb has no look for it, "
        "so that step of the turn happens with the window showing the last one"
    )


def test_every_orb_state_has_something_that_produces_it():
    unproduced = orb_states() - emitted_states() - SHELL_ONLY - DERIVED
    assert not unproduced, (
        f"the orb draws {sorted(unproduced)} and nothing emits it -- a state "
        "that never happens looks exactly like a state that is broken"
    )


def test_the_wake_flash_is_produced_before_listening():
    """The one this rule was written from.

    "I heard you" is the whole point of a wake word: it comes before anything
    slow has started. Emitting only `listening` means the window says nothing
    until the models have loaded.
    """
    source = (PACKAGE / "audio" / "voice.py").read_text(encoding="utf-8")
    wake = source.index('"value": "wake"')
    listening = source.index('"value": "listening"', wake)
    assert wake < listening


# -- the levels the orb pulses with ---------------------------------------

np = pytest.importorskip("numpy")


def test_the_level_carries_what_is_playing():
    """The speaking rings pulse with the output, and the output is measured.

    `reference_rms` already exists for the barge-in floor. Carrying it on the
    same event the microphone rides is the difference between rings that mean
    something and rings on a timer, which look identical.
    """
    from sunday import config
    from sunday.audio.listener import Listener

    listener = Listener(config.get(), wake=None, vad=None)
    listener.reference_rms = 0.25
    block = np.full(320, 0.5, dtype=np.float32)
    events = []
    for _ in range(10):
        events.extend(listener.frame(block))

    levels = [e for e in events if e.kind == "level"]
    assert levels, "the level event stopped being emitted"
    assert all(abs(e.out - 0.25) < 0.01 for e in levels)
    assert all(abs(e.rms - 0.5) < 0.01 for e in levels)


def test_the_output_level_reaches_the_socket():
    """Measured is not emitted. This is the step that was missing."""
    source = (PACKAGE / "audio" / "voice.py").read_text(encoding="utf-8")
    assert '"out": event.out' in source

# -- one fact, one producer ------------------------------------------------

#: Messages that state a single fact about a turn, and must therefore be
#: emitted from exactly one place. Everything not listed here is legitimately
#: emitted from many: `state` changes all through a turn, `notice` and `error`
#: can come from anywhere, `token` and `sentence` are streams.
ONE_PRODUCER = {
    "partial": "the turn announces what was asked, whoever asked it",
    "ready": "one greeting, assembled in one place",
    "confirm": "two emitters meant two cards, and the first card's buttons did nothing",
    "pong": "one answer to one ping",
}

#: `done` is deliberately not on that list. The runtime emits it at the end of
#: every turn it runs, and the socket emits it for the two turns the runtime
#: never sees -- Ollama unreachable, and setup unfinished. Those are not a
#: second copy of one fact; they are the only copy, for a turn that did not
#: reach the thing that would otherwise say it. A client that stops listening
#: at `done` has to get one either way.


def test_a_fact_about_a_turn_has_exactly_one_producer():
    """`partial` had two, and only spoken turns showed it.

    The ear announced the transcript and then started a turn, which announced
    it again -- so a typed question appeared once in the transcript and a
    spoken one appeared twice. No test caught it, because the fake ear calls
    `on_transcript` directly and never runs `_deliver`, which is where the
    second one was.

    That is the same failure as the duplicate confirmation card, and this is
    the general form of it: a message that states one fact is emitted from one
    place, and the way to check that is to count the places rather than to
    watch one modality and trust the other.
    """
    emitters: dict[str, list[str]] = {kind: [] for kind in ONE_PRODUCER}
    for path in PACKAGE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for kind in ONE_PRODUCER:
            # Both spellings the package uses: the dict literal that goes on
            # the socket, and the runtime's keyword form.
            hits = len(re.findall(rf'"type": "{kind}"', source))
            hits += len(re.findall(rf'type="{kind}"', source))
            emitters[kind].extend([path.name] * hits)

    for kind, why in ONE_PRODUCER.items():
        where = emitters[kind]
        assert len(where) == 1, (
            f"{kind!r} is emitted from {len(where)} places ({', '.join(where) or 'none'}) "
            f"and should come from one -- {why}"
        )

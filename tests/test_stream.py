"""Milestone 6: tokens arrive as generated, and the splitter emits whole
sentences.

The splitter is the piece most likely to embarrass the assistant out loud, so
the decimals and abbreviations get their own cases.
"""

from __future__ import annotations

import pytest

from sunday import stream


def chunk(text: str, size: int = 3) -> list[str]:
    """Chop a reply into token-sized pieces, the way a model emits it."""
    return [text[i : i + size] for i in range(0, len(text), size)]


def test_a_plain_reply_splits_into_its_sentences():
    text = "Gold is up. Silver is flat. Do you want the weekly change?"
    assert list(stream.sentences(chunk(text))) == [
        "Gold is up.",
        "Silver is flat.",
        "Do you want the weekly change?",
    ]


def test_the_first_sentence_may_be_very_short():
    out = list(stream.sentences(chunk("Sure. Here is what I found on that.")))
    assert out[0] == "Sure."


@pytest.mark.parametrize(
    "text",
    [
        "Gold is at 2,412.55 dollars an ounce today.",
        "The reading was 29.4 degrees this morning.",
        "It went from 1.5 to 3.25 over the week.",
    ],
)
def test_a_dot_between_digits_is_not_a_sentence(text):
    assert list(stream.sentences(chunk(text))) == [text]


@pytest.mark.parametrize(
    "text",
    [
        "Mr. Yusuf called about the rent.",
        "See Fig. 2 for the breakdown.",
        "Ask Dr. Sari when she is free.",
    ],
)
def test_a_short_capitalised_word_before_the_dot_is_not_a_sentence(text):
    assert list(stream.sentences(chunk(text))) == [text]


def test_a_trailing_ellipsis_does_not_split():
    text = "I was getting to that... but you interrupted me."
    assert list(stream.sentences(chunk(text))) == [text]


def test_a_terminal_with_no_space_after_it_waits():
    splitter = stream.SentenceSplitter()
    assert splitter.feed("Gold is up.") == []  # nothing follows the dot yet
    assert splitter.feed(" Silver is flat.") == ["Gold is up."]
    assert splitter.flush() == "Silver is flat."


def test_a_long_unpunctuated_sentence_is_forced_at_a_comma():
    text = (
        "so what I found is that the price has been climbing steadily "
        "through the week across every major market and the analysts I "
        "read seem to agree, though nobody will actually say it out loud "
        "and there is no sign of that changing any time in the near future"
    )
    assert len(text) > stream.FORCE_SPLIT_AT
    out = list(stream.sentences(chunk(text)))

    assert len(out) == 2
    assert out[0].endswith(",")
    assert len(out[0]) <= stream.FORCE_SPLIT_AT
    assert " ".join(out) == text


def test_a_short_sentence_is_not_forced_at_its_comma():
    text = "Yes, that is right."
    assert list(stream.sentences(chunk(text))) == [text]


def test_whatever_is_left_is_sent_at_the_end():
    splitter = stream.SentenceSplitter()
    splitter.feed("No terminal punctuation here")
    assert splitter.flush() == "No terminal punctuation here"
    assert splitter.flush() is None


def test_both_sinks_see_the_same_reply():
    text = "Gold is at 2,412.55. Silver is flat. Anything else?"
    tokens: list[str] = []
    spoken: list[str] = []
    assembled = stream.fork(
        chunk(text), on_token=tokens.append, on_sentence=spoken.append
    )

    assert assembled == text
    assert "".join(tokens) == text
    assert " ".join(spoken) == text
    assert spoken[0] == "Gold is at 2,412.55."


def test_the_token_sink_does_not_wait_for_a_sentence():
    """The text client prints the moment tokens arrive."""
    seen: list[int] = []
    spoken: list[str] = []

    def note_token(piece):
        seen.append(len(spoken))

    stream.fork(
        chunk("A long opening clause with no ending yet", 4),
        on_token=note_token,
        on_sentence=spoken.append,
    )
    assert seen[0] == 0  # tokens flowed before any sentence completed


def test_the_runtime_drives_both_sinks(cfg):
    """End of milestone 6: the graph's final pass feeds tokens and sentences
    from one generation."""
    from sunday.agent.llm import Reply
    from sunday.runtime import Runtime

    class Talker:
        def chat(self, messages, *, tools=None, think=False, max_tokens=None):
            return Reply(content="")

        def stream(self, messages, *, think=False):
            yield from chunk("Gold is at 2,412.55. Silver is flat.", 5)

        def preflight(self):
            return None

    class NoMemory:
        def retrieve(self, query, **kwargs):
            return []

        def add_turn(self, **kwargs):
            return None

        def add_session_summary(self, **kwargs):
            return None

        def count(self):
            return 0

    tokens: list[str] = []
    spoken: list[str] = []
    runtime = Runtime(cfg, agent=Talker(), memory=NoMemory())  # type: ignore[arg-type]
    state = runtime.run_turn(
        "how much is gold", on_token=tokens.append, on_sentence=spoken.append
    )

    assert state["final_response"] == "Gold is at 2,412.55. Silver is flat."
    assert "".join(tokens) == state["final_response"]
    assert spoken == ["Gold is at 2,412.55.", "Silver is flat."]

"""Layer 3: did it just transcribe itself?

Layers 1 and 2 are signal processing -- subtract what was played, then raise the
bar for what counts as a person. Whatever survives both still has to survive
this, which is not signal processing at all: compare the fresh transcript
against the sentence Kokoro is currently speaking, and if they are the same
words, it was not a person.

It is a handful of lines and it catches the case where the room is echoey and
the AEC was not quite enough. Cheap insurance against the most annoying
possible bug, which is an assistant answering itself in a loop.

Token overlap rather than edit distance, and deliberately. What comes back
through a speaker and a microphone and a transcriber is not a character-level
corruption of what went out -- it is the same words with different punctuation,
a dropped article, a number spelled out instead of written. Overlap is blind to
exactly the things that change and sensitive to the thing that does not.
"""

from __future__ import annotations

import re

#: Words carrying no evidence either way. A reply and an unrelated question
#: share "the" and "is" for free, and with short sentences that alone can clear
#: 0.6. Kept tiny for the same reason the airlock's filler list is kept tiny.
_NOISE = {
    "a", "an", "the", "is", "it", "of", "in", "on", "at", "to", "and", "or",
    "for", "with", "that", "this", "was", "are", "be", "as", "by",
}

_WORD = re.compile(r"[a-z0-9]+")

#: A comma between digits is a thousands separator, and whether it survives is
#: a coin toss: Kokoro is given "2,412.55" and the transcriber writes back
#: "2412.55". Tokenised as they stand those share nothing at all, and a reply
#: about a price would sail past the check on the strength of its numbers.
_GROUPED = re.compile(r"(?<=\d),(?=\d)")


def words(text: str) -> set[str]:
    """The words that carry evidence, lowercased and stripped of punctuation."""
    flat = _GROUPED.sub("", text.lower())
    return {w for w in _WORD.findall(flat) if w not in _NOISE}


def similarity(heard: str, spoken: str) -> float:
    """How much of what was heard was already being said.

    Asymmetric on purpose. The question is whether the transcript is contained
    in the sentence being spoken, not whether the two are the same length --
    the microphone catches part of a sentence far more often than all of it, and
    a symmetric measure scores that low exactly when it should score high.
    """
    a, b = words(heard), words(spoken)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a)


def is_echo(heard: str, spoken: str, *, cutoff: float = 0.6) -> bool:
    """Was that Sunday? `spoken` empty means it was not talking, so no.

    What this does not catch, stated rather than discovered later: a number
    the synthesiser said aloud and the transcriber wrote back in words, or the
    reverse. "2412.55" and "twenty four twelve" share no tokens and overlap
    cannot see through that. Layers 1 and 2 are what stand behind it.
    """
    if not spoken.strip():
        return False
    return similarity(heard, spoken) >= cutoff


__all__ = ["is_echo", "similarity", "words"]

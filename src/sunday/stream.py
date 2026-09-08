"""One stream, two sinks.

The text client prints tokens the moment they arrive; the speech client needs
whole sentences, because a synthesiser cannot speak half of one. Both read the
same stream, so what you see and what you hear can never disagree.

Splitting on every `.` mispronounces every decimal and every abbreviation, so
the rules run in this order:

1. Split on `.` `?` `!` -- but only when whitespace or the end of the stream
   follows.
2. Never split inside a known non-break: a dot between digits, a known
   abbreviation before the dot, a single initial, a trailing ellipsis. A
   length heuristic was tried first and ate "Sure." and "Yes.", which are
   exactly the short openers rule 1 exists to release early.
3. Force a split at the last comma at or before 180 characters, so one long
   unpunctuated sentence still starts playing. Searching forward from 180
   instead would never fire for a sentence whose only comma sits at 140.
4. Send whatever is left when the stream ends.

The first sentence is allowed to be very short on purpose. A two-word opener
reaching the synthesiser straight away is the whole speed win.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable, Iterator

TERMINALS = ".?!"
FORCE_SPLIT_AT = 180

#: Capitalised words that take a dot without ending a sentence. A pure length
#: heuristic is tempting and wrong: it eats "Sure." and "Yes." and "Done.",
#: which are exactly the short openers rule 1 exists to release early.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "fig", "no", "inc",
    "ltd", "co", "vs", "etc", "approx", "dept", "est", "min", "max", "ave",
    "rd", "e.g", "i.e", "a.m", "p.m",
}

#: The last word before the dot, whatever its case.
_LAST_WORD = re.compile(r"([A-Za-z][A-Za-z.]*)$")

#: A single initial: "J. Smith".
_INITIAL = re.compile(r"(?:^|\s)[A-Z]$")


class SentenceSplitter:
    """Feed it tokens, take whole sentences out."""

    def __init__(self, force_split_at: int = FORCE_SPLIT_AT) -> None:
        self.buffer = ""
        self.force_split_at = force_split_at

    # -- the rules ------------------------------------------------------

    def _is_break(self, index: int) -> bool:
        """Is `self.buffer[index]` the end of a sentence?

        Called only when a character already follows it, so rule 1's
        "whitespace follows" is answerable without waiting.
        """
        char = self.buffer[index]
        if char not in TERMINALS:
            return False
        if not self.buffer[index + 1].isspace():
            return False

        if char == ".":
            before = self.buffer[:index]
            after = self.buffer[index + 1 :].lstrip()

            # A dot between digits: 3.5, 2,412.55, v1.2
            if before[-1:].isdigit() and after[:1].isdigit():
                return False
            # A trailing ellipsis.
            if before.endswith(".."):
                return False
            # An initial: "J. Smith".
            if _INITIAL.search(before):
                return False
            # A known abbreviation: Mr. Dr. No. Fig. e.g.
            match = _LAST_WORD.search(before)
            if match and match.group(1).lower().rstrip(".") in _ABBREVIATIONS:
                return False
        return True

    def _forced_break(self) -> int | None:
        """Rule 3: one long unpunctuated sentence still has to start playing.

        Prefer the last comma inside the limit, so the piece that goes out
        stays under it. Only if there is none does a later comma do.
        """
        if len(self.buffer) <= self.force_split_at:
            return None
        inside = self.buffer.rfind(",", 0, self.force_split_at)
        if inside != -1:
            return inside
        beyond = self.buffer.find(",", self.force_split_at)
        return beyond if beyond != -1 else None

    # -- the stream -----------------------------------------------------

    def feed(self, piece: str) -> list[str]:
        """Add a token, take back whatever sentences are now complete."""
        self.buffer += piece
        out: list[str] = []

        while True:
            cut = None
            for index in range(len(self.buffer) - 1):
                if self._is_break(index):
                    cut = index + 1
                    break
            if cut is None:
                forced = self._forced_break()
                cut = forced + 1 if forced is not None else None
            if cut is None:
                break

            sentence = self.buffer[:cut].strip()
            self.buffer = self.buffer[cut:].lstrip()
            if sentence:
                out.append(sentence)
        return out

    def flush(self) -> str | None:
        """Rule 4: send whatever is left when the stream ends."""
        rest = self.buffer.strip()
        self.buffer = ""
        return rest or None


def sentences(pieces: Iterable[str]) -> Iterator[str]:
    """The whole stream as sentences. Convenience for tests and batch use."""
    splitter = SentenceSplitter()
    for piece in pieces:
        yield from splitter.feed(piece)
    last = splitter.flush()
    if last:
        yield last


#: Characters that only ever mean markdown in a reply that may be spoken.
#: `_` is not here on purpose: it is in half the filenames on the machine.
#: `#` is not here either -- it is a heading only at the start of a line, and
#: that case is handled by the bullet rule below.
_INLINE_MARKS = str.maketrans("", "", "*`")

#: A list marker, at position zero only. Anchored rather than multiline,
#: because the streaming version decides one line at a time and needs to know
#: it is *at* a line start rather than searching for one.
_BULLET_HEAD = re.compile(r"^[ 	]*(?:[-*+]|\d{1,2}[.)]|#{1,6})[ 	]+")

#: The same rule over a whole string, for the batch helper.
_BULLET = re.compile(r"(?m)^[ 	]*(?:[-*+]|\d{1,2}[.)]|#{1,6})[ 	]+")


def despeckle(text: str) -> str:
    """Take the markdown out of a reply.

    The system prompt forbids it and the model obeys most of the time. Most of
    the time is not good enough for a channel that gets read aloud, where a
    backtick is pronounced and a dash at the start of a line is a spoken word.
    The rule is stated once, a thousand tokens earlier; this is the same rule
    somewhere it cannot be forgotten.

    Deliberately narrow: asterisks and backticks anywhere, and a list marker at
    the start of a line. Underscores are left alone -- half the filenames on
    this machine have one -- and nothing here parses anything.
    """
    return _BULLET.sub("", text.translate(_INLINE_MARKS))


class Despeckler:
    """`despeckle`, applied to a stream that arrives in pieces.

    Line starts are the awkward part. A bullet is only a bullet at the start of
    a line, and a token boundary falls wherever the model put it -- often
    between the newline and the dash. So the stripper tracks whether it is at a
    line start, and when it is, holds back until it has enough characters to
    decide. At most seven, and never past the end of a line.
    """

    #: The longest marker plus its space: "###### ".
    _HOLD = 8

    def __init__(self) -> None:
        self._buf = ""
        self._at_start = True

    def feed(self, piece: str) -> str:
        self._buf += piece
        out: list[str] = []
        while self._buf:
            if self._at_start:
                if len(self._buf) < self._HOLD and "\n" not in self._buf:
                    break  # not enough yet to know whether it is a marker
                self._buf = _BULLET_HEAD.sub("", self._buf)
                self._at_start = False
                continue
            newline = self._buf.find("\n")
            if newline == -1:
                out.append(self._buf.translate(_INLINE_MARKS))
                self._buf = ""
                break
            out.append(self._buf[: newline + 1].translate(_INLINE_MARKS))
            self._buf = self._buf[newline + 1 :]
            self._at_start = True
        return "".join(out)

    def flush(self) -> str:
        if self._at_start:
            self._buf = _BULLET_HEAD.sub("", self._buf)
        rest, self._buf = self._buf, ""
        self._at_start = True
        return rest.translate(_INLINE_MARKS)


def fork(
    pieces: Iterable[str],
    *,
    on_token: Callable[[str], None] | None = None,
    on_sentence: Callable[[str], None] | None = None,
) -> str:
    """Drive both sinks from one stream and return the assembled reply.

    Markdown is stripped here rather than in either sink, so what is shown,
    what is spoken and what is filed in memory are the same text. Cleaning it
    in one place and not the others is how a transcript ends up disagreeing
    with what the person heard.
    """
    splitter = SentenceSplitter()
    cleaner = Despeckler()
    collected: list[str] = []

    def emit(piece: str) -> None:
        if not piece:
            return
        collected.append(piece)
        if on_token is not None:
            on_token(piece)
        if on_sentence is not None:
            for sentence in splitter.feed(piece):
                on_sentence(sentence)

    for raw in pieces:
        emit(cleaner.feed(raw))
    emit(cleaner.flush())

    if on_sentence is not None:
        last = splitter.flush()
        if last:
            on_sentence(last)
    return "".join(collected)

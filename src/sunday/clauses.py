"""What a message is made of, when it is made of more than one thing.

Two parts of this package care that a person can put two instructions in one
sentence, and they want opposite things from the fact. `fastpaths` refuses to
answer a compound message at all, because answering the clause it matched
convinces the model the turn is finished. Retrieval does the reverse: it
searches the clauses *as well*, because one embedding of two subjects sits
between them and is close to neither.

Opposite uses, one fact, so the connectives are written down once. The
alternative is two regexes that agree today -- and this repository has paid
four times for a rule that lived in each caller: `mkdir(parents=True)`
surviving on `write_file` after copy and move lost it, the compound guard
covering two fast paths out of three, amber reaching every screen except
`Setup.svelte`, and the backstage trim hanging off the typed path only.
"""

from __future__ import annotations

import re

#: The words people join two instructions with, English and Indonesian -- the
#: user of this build asks in both. `;` is here because it is the one piece of
#: punctuation that is never anything else in a spoken or typed request.
#:
#: Every branch is non-capturing, so `re.split` returns the pieces and not the
#: separators.
CONNECTIVES = re.compile(
    r"(?:\band\b|\bthen\b|\balso\b|\bafter that\b|;|\bdan\b|\blalu\b|\bterus\b)",
    re.IGNORECASE,
)

#: Below this, a piece is a list item rather than a question. "gold and
#: silver" splits into two one-word fragments, and a one-word query is close
#: to nothing in particular and near enough to anything short -- so the split
#: is taken only when every piece of it could stand as a question.
MIN_CLAUSE_WORDS = 2


def is_compound(text: str) -> bool:
    """Does this message carry more than one instruction?

    Asked by `fastpaths.match` before any pattern is tried, which is the point:
    it guarded two of the three call sites for a whole milestone, and the one
    it missed was the worst to answer half a question with.
    """
    return bool(CONNECTIVES.search(text))


def split(text: str) -> list[str]:
    """The clauses, or nothing at all.

    All or nothing on purpose. A split that yields one usable piece and one
    fragment is worse than no split: the fragment is a query that matches
    loosely, and its results are merged in beside the good ones. So either
    every piece stands on its own or the line is left whole.
    """
    if not is_compound(text):
        return []
    parts = [part.strip(" .,?!'\"") for part in CONNECTIVES.split(text)]
    parts = [part for part in parts if part]
    if len(parts) < 2:
        return []
    if any(len(part.split()) < MIN_CLAUSE_WORDS for part in parts):
        return []
    return parts


def queries_for(text: str) -> list[str]:
    """What to search memory for, given what the user said.

    The whole line first, then each clause. The whole line stays because it is
    the only query that can match a document written about both subjects at
    once -- a session summary, typically. Dropping it to save an embedding
    would trade the one document that answers "what did we do last session"
    for a few milliseconds.
    """
    return [text, *split(text)]

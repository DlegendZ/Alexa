"""Three deterministic patterns, run in code before the model is asked anything.

These are not an optimisation. With a 2b and no larger model behind it, they
are the insurance policy for the requests where a fumbled answer would be most
obvious: the two most common ones, and the one the model cannot answer at all
from its own head.

That third one is "what can you do". A 2b does not read its bound schemas back
to you -- it writes a confident paragraph about tools it does not have. The
answer to a question about the machine's own state has to be read off the
machine, so it is a tool call, and this fires it before the model gets a vote.

The result is handed to the agent as an ordinary tool result, so the turn
carries on normally: the model can still call more tools, and a mixed question
loses nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sunday.tools import assets

_WEATHER = re.compile(
    r"\bweather\s+(?:in|for|at|of)\s+([A-Za-zÀ-ɏ .'-]{2,40})",
    re.IGNORECASE,
)

_PRICE = re.compile(
    r"\b(?:price|cost|how\s+much)\s+(?:of|is|for|are)?\s*(?:the\s+|a\s+|an\s+)?"
    r"([A-Za-z]{2,12})",
    re.IGNORECASE,
)

#: Trailing words a person adds that are not part of a city name.
_TAIL = re.compile(
    r"\s*\b(?:right\s+now|now|today|tonight|tomorrow|please|then|and\b.*)$",
    re.IGNORECASE,
)


#: "what can you do", in the shapes people actually type it, including the
#: Indonesian ones -- the user of this build asks in both languages.
_CAPABILITIES = re.compile(
    r"(?:"
    r"what (?:can|could) you do"
    r"|what (?:tools|abilities|capabilities|functions|commands)"
    r"|list (?:your |all )?(?:tools|capabilities|abilities|functions)"
    r"|which (?:tools|folders|directories)"
    r"|what (?:folders|directories|dirs) (?:can|do) you"
    r"|apa (?:saja |aja )?(?:yang bisa|kemampuan|tool|kamu bisa)"
    r"|kemampuan (?:kamu|mu|apa)"
    r"|bisa apa (?:aja|saja)"
    r")",
    re.IGNORECASE,
)


#: A message carrying more than one instruction. A fast path answers exactly
#: the clause it matched, and the model, seeing a tool result already sitting
#: in the transcript, reads the turn as finished -- so "what is the price of
#: gold and what is the weather in Jakarta" came back about the weather alone,
#: with "no specific price was returned for this request" bolted on. Telling
#: the model in a system line that the fast path was partial did not fix it:
#: it was tried, and the second half was still dropped.
#:
#: So a compound message does not get a fast path at all -- not one of the
#: three, which is why this is tested before any pattern is tried. The
#: insurance exists for the single-clause case where a fumbled argument would
#: be obvious; on a compound the model has to do the work, which is what it
#: does correctly when nothing has answered ahead of it.
_COMPOUND = re.compile(
    r"(?:\band\b|\bthen\b|\balso\b|\bafter that\b|;|\bdan\b|\blalu\b|\bterus\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FastPath:
    tool: str
    args: dict[str, str]


def match(task: str) -> FastPath | None:
    # Two instructions in one message: no shortcut, and this comes before
    # every pattern rather than before two of them. Answering half of it in
    # code is what convinces the model the whole thing is done, and the
    # capability list is the worst of the three to answer half a question
    # with -- it is long, it is nearest, and a 2b reading it decides the turn
    # was about itself. "What can you do, and what is the weather in Jakarta"
    # used to come back as a tour of the tool belt with no weather in it.
    if _COMPOUND.search(task):
        return None

    # Then capabilities, because "what can you do" contains no city and no
    # asset but would otherwise fall through to the model, which invents an
    # answer.
    if _CAPABILITIES.search(task):
        return FastPath("list_capabilities", {})

    weather = _WEATHER.search(task)
    if weather:
        city = _TAIL.sub("", weather.group(1)).strip(" .,'\"")
        if city:
            return FastPath("get_weather", {"city": city})

    price = _PRICE.search(task)
    if price:
        symbol = assets.resolve_symbol(price.group(1))
        if symbol:
            return FastPath("get_asset_price", {"symbol": symbol})
    return None

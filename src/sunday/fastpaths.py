"""Two deterministic patterns, run in code before the model is asked anything.

These are not an optimisation. With a 2b and no larger model behind it, they
are the insurance policy for the two most common requests -- the ones where a
fumbled argument would be most obvious.

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
    r"\s*(?:right\s+now|now|today|tonight|tomorrow|please|then|and\b.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FastPath:
    tool: str
    args: dict[str, str]


def match(task: str) -> FastPath | None:
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

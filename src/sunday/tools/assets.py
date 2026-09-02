"""Asset prices -- gold-api.com, free, no key.

The symbol is checked against a closed set before the request, so an unknown
symbol never reaches the network. Results are labelled `public`.
"""

from __future__ import annotations

from sunday import net
from sunday.tools import Tool, register

PRICE_URL = "https://api.gold-api.com/price/{symbol}"

SYMBOLS = {
    "XAU": "Gold",
    "XAG": "Silver",
    "XPT": "Platinum",
    "XPD": "Palladium",
    "HG": "Copper",
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
}

#: Words people actually say, mapped to symbols. Used by the fast path and to
#: rescue a fumbled argument from a 2b.
ALIASES = {
    "gold": "XAU",
    "silver": "XAG",
    "platinum": "XPT",
    "palladium": "XPD",
    "copper": "HG",
    "bitcoin": "BTC",
    "btc": "BTC",
    "ethereum": "ETH",
    "eth": "ETH",
    "ether": "ETH",
}


def resolve_symbol(raw: str) -> str | None:
    token = (raw or "").strip().lower()
    if token.upper() in SYMBOLS:
        return token.upper()
    return ALIASES.get(token)


def get_asset_price(symbol: str) -> str:
    resolved = resolve_symbol(symbol)
    if resolved is None:
        return (
            f"Unknown symbol '{symbol}'. Supported: "
            + ", ".join(f"{k} ({v})" for k, v in SYMBOLS.items())
        )

    try:
        payload = net.get_json(PRICE_URL.format(symbol=resolved), timeout=10)
    except net.HttpError as exc:
        return f"error: could not reach the price service ({exc})"

    price = payload.get("price")
    if price is None:
        return f"Price lookup failed for {resolved}: no price in the response"

    return f"{SYMBOLS[resolved]} ({resolved}): ${price:,.2f} USD"


register(
    Tool(
        name="get_asset_price",
        description=(
            "Current USD price of one asset. symbol must be one of XAU (gold), "
            "XAG (silver), XPT (platinum), XPD (palladium), HG (copper), "
            "BTC (bitcoin), ETH (ethereum). Runs on this machine."
        ),
        parameters={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "One of XAU, XAG, XPT, XPD, HG, BTC, ETH",
                    "enum": list(SYMBOLS),
                }
            },
            "required": ["symbol"],
        },
        fn=get_asset_price,
        provenance="public",
    )
)

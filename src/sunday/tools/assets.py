"""Precious metals / crypto price tool — gold-api.com (free, no API key)."""

import requests
from langchain_core.tools import tool

PRICE_URL = "https://api.gold-api.com/price/{symbol}"

SYMBOLS = {
    "XAG": "Silver",
    "XAU": "Gold",
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "XPD": "Palladium",
    "HG": "Copper",
    "XPT": "Platinum",
}


@tool
def get_asset_price(symbol: str) -> str:
    """Get the current USD price of an asset. Symbol must be one of:
    XAG (Silver), XAU (Gold), BTC (Bitcoin), ETH (Ethereum),
    XPD (Palladium), HG (Copper), XPT (Platinum)."""
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        return f"Unknown symbol '{symbol}'. Supported: {', '.join(SYMBOLS)}"

    resp = requests.get(PRICE_URL.format(symbol=symbol), timeout=10).json()
    price = resp.get("price")
    if price is None:
        return f"Price lookup failed for {symbol}: {resp}"

    return f"{SYMBOLS[symbol]} ({symbol}): ${price:,.2f} USD"

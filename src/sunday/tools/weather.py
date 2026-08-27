"""Weather tool — Open-Meteo (free, no API key required)."""

import requests
from langchain_core.tools import tool

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_CODES = {
    0: "clear sky",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    71: "slight snow",
    73: "moderate snow",
    75: "heavy snow",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


@tool
def get_weather(city: str) -> str:
    """Get current weather for a city. Pass a city name, e.g. 'Jakarta'."""
    geo = requests.get(
        GEOCODE_URL, params={"name": city, "count": 1}, timeout=10
    ).json()
    results = geo.get("results")
    if not results:
        return f"Could not find location: {city}"

    loc = results[0]
    lat, lon = loc["latitude"], loc["longitude"]
    label = f"{loc['name']}, {loc.get('country', '')}".strip(", ")

    fc = requests.get(
        FORECAST_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
        },
        timeout=10,
    ).json()
    current = fc.get("current")
    if not current:
        return f"Weather data unavailable for {label}"

    code = current.get("weather_code")
    condition = WEATHER_CODES.get(code, f"code {code}")
    return (
        f"{label}: {current['temperature_2m']}°C, {condition}, "
        f"humidity {current['relative_humidity_2m']}%, "
        f"wind {current['wind_speed_10m']} km/h"
    )

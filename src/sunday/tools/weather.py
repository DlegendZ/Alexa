"""Weather -- Open-Meteo, free, no key.

Two HTTP calls with one city name in them. That is not sending your data
anywhere: the argument is closed, and there is nowhere in a city name to hide a
payload. Results are labelled `public`.
"""

from __future__ import annotations

from sunday import net
from sunday.tools import Tool, register

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes.
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
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


def get_weather(city: str) -> str:
    city = (city or "").strip()
    if not city:
        return "error: no city given"

    try:
        geo = net.get_json(GEOCODE_URL, params={"name": city, "count": 1}, timeout=10)
    except net.HttpError as exc:
        return f"error: could not reach the geocoder ({exc})"

    results = geo.get("results")
    if not results:
        return f"Could not find location: {city}"

    loc = results[0]
    label = ", ".join(part for part in (loc.get("name"), loc.get("country")) if part)

    try:
        forecast = net.get_json(
            FORECAST_URL,
            params={
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            },
            timeout=10,
        )
    except net.HttpError as exc:
        return f"error: could not reach the weather service ({exc})"

    current = forecast.get("current")
    if not current:
        return f"Weather data unavailable for {label}"

    code = current.get("weather_code")
    condition = WEATHER_CODES.get(code, f"code {code}")
    return (
        f"{label}: {current['temperature_2m']}°C, {condition}, "
        f"humidity {current['relative_humidity_2m']}%, "
        f"wind {current['wind_speed_10m']} km/h"
    )


register(
    Tool(
        name="get_weather",
        description=(
            "Current weather for one city. Pass a plain city name such as "
            "'Jakarta' or 'Tokyo'. Runs on this machine; only the city name "
            "reaches the weather service."
        ),
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name, e.g. Jakarta"}
            },
            "required": ["city"],
        },
        fn=get_weather,
        provenance="public",
    )
)

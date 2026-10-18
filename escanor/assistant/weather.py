"""
assistant/weather.py

Real weather from Open-Meteo (free, no API key): current conditions and a
3-day forecast for a city. Without it the model answered "weather today"
from imagination (B21).

The city comes from the request ("weather in Mumbai"), else [assistant]
city in config.toml. Only the city name and its coordinates are sent to
Open-Meteo, never anything else.
"""
from __future__ import annotations
import json
import urllib.parse
import urllib.request
from typing import Callable
from ..tools.base import READ, Tool, ToolResult

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes, as used by Open-Meteo.
CONDITIONS = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "violent showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm",
    96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Escanor/0.1"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read(1_000_000))


class Weather:
    def __init__(self, default_city: str = "", get_json: Callable[[str], dict] = _get_json):
        self.default_city = default_city
        self._get = get_json

    def locate(self, city: str) -> dict | None:
        data = self._get(f"{GEOCODE}?" + urllib.parse.urlencode({"name": city, "count": 1, "language": "en"}))
        results = data.get("results") or []
        return results[0] if results else None

    def report(self, city: str | None = None) -> str:
        city = (city or self.default_city or "").strip()
        if not city:
            raise ValueError("No city given and none set. Ask the user for their city "
                             "(they can also set [assistant] city in ~/.escanor/config.toml).")
        place = self.locate(city)
        if place is None:
            raise ValueError(f"Couldn't find a place called '{city}'.")
        query = {
            "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto", "forecast_days": 3,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        }
        data = self._get(f"{FORECAST}?" + urllib.parse.urlencode(query))
        current, daily = data.get("current", {}), data.get("daily", {})
        name = ", ".join(p for p in (place.get("name"), place.get("admin1"), place.get("country")) if p)
        lines = [
            f"{name} — now {current.get('temperature_2m')}°C (feels {current.get('apparent_temperature')}°C), "
            f"{CONDITIONS.get(current.get('weather_code'), 'unknown conditions')}, "
            f"humidity {current.get('relative_humidity_2m')}%, wind {current.get('wind_speed_10m')} km/h."
        ]
        for i, day in enumerate(daily.get("time", [])[:3]):
            label = ("Today", "Tomorrow")[i] if i < 2 else day
            rain = (daily.get("precipitation_probability_max") or [None] * 3)[i]
            lines.append(
                f"{label}: {CONDITIONS.get(daily['weather_code'][i], 'unknown')}, "
                f"{daily['temperature_2m_min'][i]}–{daily['temperature_2m_max'][i]}°C"
                + (f", {rain}% chance of rain" if rain is not None else "")
            )
        return "\n".join(lines)


class WeatherTool(Tool):
    name = "weather"
    description = (
        "Current weather and a 3-day forecast for a city (default: the user's city). Always use this for "
        "weather; never describe the weather without it."
    )
    input_schema = {"type": "object", "properties": {"city": {"type": "string"}}}
    side_effect = READ

    def __init__(self, weather: Weather):
        self.weather = weather

    def run(self, city: str | None = None) -> ToolResult:
        try:
            return ToolResult(ok=True, output=self.weather.report(city))
        except ValueError as e:
            return ToolResult(ok=False, output=str(e))
        except (OSError, KeyError, IndexError, TypeError) as e:
            return ToolResult(ok=False, output=f"Weather service unavailable: {e}")

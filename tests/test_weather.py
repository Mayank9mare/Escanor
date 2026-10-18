import pytest
from escanor.assistant.weather import Weather, WeatherTool

GEO = {"results": [{"name": "Bengaluru", "admin1": "Karnataka", "country": "India", "latitude": 12.97, "longitude": 77.59}]}
FORECAST = {
    "current": {"temperature_2m": 25.8, "apparent_temperature": 27.9, "relative_humidity_2m": 68, "weather_code": 3, "wind_speed_10m": 9.0},
    "daily": {"time": ["2026-10-18", "2026-10-19", "2026-10-20"], "weather_code": [80, 95, 51],
              "temperature_2m_max": [26.1, 28.4, 28.2], "temperature_2m_min": [20.5, 19.7, 19.6],
              "precipitation_probability_max": [72, 84, 51]},
}


def fake(urls):
    def get(url):
        urls.append(url)
        return GEO if "geocoding" in url else FORECAST
    return get


def test_report_uses_requested_city_then_default():
    urls = []
    text = Weather("Bengaluru", get_json=fake(urls)).report()
    assert text.splitlines()[0] == ("Bengaluru, Karnataka, India — now 25.8°C (feels 27.9°C), overcast, "
                                    "humidity 68%, wind 9.0 km/h.")
    assert text.splitlines()[1] == "Today: light showers, 20.5–26.1°C, 72% chance of rain"
    assert "name=Bengaluru" in urls[0] and "latitude=12.97" in urls[1]


def test_tool_asks_for_city_when_none_known():
    result = WeatherTool(Weather("", get_json=fake([]))).run()
    assert result.ok is False and "Ask the user for their city" in result.output


def test_tool_reports_unknown_place_and_outage():
    assert "Couldn't find" in WeatherTool(Weather(get_json=lambda url: {"results": []})).run(city="Atlantis").output

    def down(url):
        raise OSError("offline")
    assert "unavailable" in WeatherTool(Weather(get_json=down)).run(city="Pune").output

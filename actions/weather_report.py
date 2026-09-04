"""
actions/weather_report.py — Weather lookup.

FIX (2026-08-31): the previous implementation called `webbrowser.open()`
on a Google search URL and returned a generic "showing weather" message
without ever fetching real data. That works on a desktop with a browser,
but this tool is also wrapped as an ADK FunctionTool
(agent/adk_tools.py) and exposed on the headless Cloud Run backend,
where there is no browser to open — every call failed there, including
the exact example in JUDGE_TESTING.md. This version calls wttr.in's
JSON endpoint directly (no API key required) and returns an actual text
summary, so the tool works identically on desktop and on Cloud Run.
"""

from urllib.parse import quote_plus

import requests

from agent.tool_result import ok, fail

_WTTR_TIMEOUT = 8


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None,
):
    city = parameters.get("city")
    when = parameters.get("time", "today")

    if not city or not isinstance(city, str) or not city.strip():
        msg = "Sir, the city is missing for the weather report."
        _log(msg, player)
        return fail(msg)

    city = city.strip()
    when = (when or "today").strip()

    try:
        resp = requests.get(
            f"https://wttr.in/{quote_plus(city)}",
            params={"format": "j1"},
            headers={"User-Agent": "curl/8.0"},  # wttr.in serves plain text to browser UAs; curl UA gets JSON
            timeout=_WTTR_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        current = (data.get("current_condition") or [{}])[0]
        area = (data.get("nearest_area") or [{}])[0]
        area_name = (
            (area.get("areaName") or [{}])[0].get("value")
            if area.get("areaName") else city
        )

        desc = (current.get("weatherDesc") or [{}])[0].get("value", "unknown conditions")
        temp_c = current.get("temp_C")
        temp_f = current.get("temp_F")
        feels_c = current.get("FeelsLikeC")
        humidity = current.get("humidity")
        wind_kmph = current.get("windspeedKmph")

        msg = (
            f"Weather in {area_name or city}: {desc}, {temp_c}\u00b0C ({temp_f}\u00b0F), "
            f"feels like {feels_c}\u00b0C, humidity {humidity}%, wind {wind_kmph} km/h."
        )

        # "when" beyond "today" isn't summarized yet (wttr.in's j1 payload
        # does include a 3-day "weather" forecast array we could pull a
        # min/max/desc from for tomorrow/this-week — left as a follow-up
        # rather than guessed at here).
        if when.lower() not in ("today", "now", "current", ""):
            msg += f" (current conditions — forecast for '{when}' isn't summarized yet.)"

    except Exception as e:
        msg = f"Sir, I couldn't retrieve the weather for {city}: {e}"
        _log(msg, player)
        return fail(msg)

    _log(msg, player)

    if session_memory:
        try:
            session_memory.set_last_search(
                query=f"weather in {city} {when}", response=msg
            )
        except Exception:
            pass

    return ok(msg)


def _log(message: str, player=None) -> None:
    print(f"[Weather] {message}")
    if player:
        try:
            player.write_log(f"LIYA: {message}")
        except Exception:
            pass

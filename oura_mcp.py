"""Oura Ring MCP server.

Exposes the Oura API v2 (https://api.ouraring.com/v2/docs) as MCP tools so an
assistant can read your sleep, readiness, activity, heart rate and more.

Auth: OAuth2. Run `oura-mcp login` once; tokens are saved locally and refreshed
automatically (see oura_auth.py). OURA_ACCESS_TOKEN overrides this if set.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

from oura_auth import TokenManager, login

API_BASE = os.environ.get("OURA_API_BASE", "https://api.ouraring.com/v2/usercollection")
MAX_PAGES = 20

mcp = MCPServer(
    name="oura",
    instructions=(
        "Read-only access to the user's Oura Ring data. Dates are YYYY-MM-DD in the "
        "user's local day. If no dates are given, tools default to the last 7 days. "
        "Scores are 0-100; durations are in seconds unless stated otherwise."
    ),
)

_client: httpx.AsyncClient | None = None
_tokens = TokenManager()


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=API_BASE, timeout=30.0)
    return _client


def _date_range(start_date: str | None, end_date: str | None, default_days: int = 7) -> tuple[str, str]:
    end = date.fromisoformat(end_date) if end_date else date.today()
    start = date.fromisoformat(start_date) if start_date else end - timedelta(days=default_days - 1)
    if start > end:
        raise ValueError(f"start_date {start} is after end_date {end}")
    return start.isoformat(), end.isoformat()


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    token = await _tokens.access_token()
    resp = await _get_client().get(path, params=clean, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 401 and _tokens.can_refresh:
        token = await _tokens.access_token(force_refresh=True)
        resp = await _get_client().get(path, params=clean, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 401:
        raise RuntimeError("Oura rejected the token (401). Run `oura-mcp login` again.")
    if resp.status_code == 403:
        raise RuntimeError(
            "Oura returned 403: the token lacks the scope for this data, or the "
            "Oura membership needed for it is inactive."
        )
    if resp.status_code == 429:
        raise RuntimeError("Oura rate limit hit (429). Try again in a few minutes.")
    resp.raise_for_status()
    return resp.json()


async def _collect(path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch every page of a collection endpoint."""
    items: list[dict[str, Any]] = []
    next_token = None
    for _ in range(MAX_PAGES):
        body = await _get(path, {**params, "next_token": next_token})
        items.extend(body.get("data", []))
        next_token = body.get("next_token")
        if not next_token:
            break
    return items


async def _daily(path: str, start_date: str | None, end_date: str | None) -> list[dict[str, Any]]:
    s, e = _date_range(start_date, end_date)
    return await _collect(path, {"start_date": s, "end_date": e})


# --- Profile -----------------------------------------------------------------

@mcp.tool()
async def get_personal_info() -> dict[str, Any]:
    """The user's Oura profile: age, weight, height, biological sex, email."""
    return await _get("personal_info")


@mcp.tool()
async def get_ring_configuration() -> list[dict[str, Any]]:
    """The user's ring(s): color, design, firmware, hardware type, size, setup date."""
    return await _collect("ring_configuration", {})


# --- Daily summaries ---------------------------------------------------------

@mcp.tool()
async def get_daily_sleep(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """Daily sleep score (0-100) and its contributors (deep sleep, efficiency,
    latency, REM, restfulness, timing, total sleep) per day."""
    return await _daily("daily_sleep", start_date, end_date)


@mcp.tool()
async def get_daily_readiness(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """Daily readiness score, temperature deviation, and contributors (HRV balance,
    resting heart rate, recovery index, sleep balance, activity balance...)."""
    return await _daily("daily_readiness", start_date, end_date)


@mcp.tool()
async def get_daily_activity(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """Daily activity score, steps, active/total calories, time in each activity
    level, and contributors. Large per-minute arrays (class_5_min, met) are dropped."""
    items = await _daily("daily_activity", start_date, end_date)
    for item in items:
        item.pop("class_5_min", None)
        item.pop("met", None)
    return items


@mcp.tool()
async def get_daily_stress(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """Daily seconds spent in high stress and high recovery, plus a day summary."""
    return await _daily("daily_stress", start_date, end_date)


@mcp.tool()
async def get_daily_resilience(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """Daily resilience level and contributors (sleep recovery, daytime recovery, stress)."""
    return await _daily("daily_resilience", start_date, end_date)


@mcp.tool()
async def get_daily_spo2(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """Average nightly blood oxygen (SpO2) percentage and breathing disturbance index."""
    return await _daily("daily_spo2", start_date, end_date)


@mcp.tool()
async def get_cardiovascular_age(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """Estimated vascular (cardiovascular) age per day."""
    return await _daily("daily_cardiovascular_age", start_date, end_date)


@mcp.tool()
async def get_vo2_max(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """VO2 max estimates."""
    return await _daily("vO2_max", start_date, end_date)


# --- Detailed records --------------------------------------------------------

@mcp.tool()
async def get_sleep_periods(
    start_date: str | None = None,
    end_date: str | None = None,
    include_timeseries: bool = False,
) -> list[dict[str, Any]]:
    """Individual sleep periods (main sleep and naps): bedtime start/end, total,
    deep, light, REM and awake durations, efficiency, latency, lowest and average
    heart rate, average HRV, breathing rate. Set include_timeseries=True for the
    5-minute heart rate/HRV series and sleep phase strings (much larger output)."""
    items = await _daily("sleep", start_date, end_date)
    if not include_timeseries:
        for item in items:
            for key in ("heart_rate", "hrv", "movement_30_sec", "sleep_phase_5_min", "sleep_phase_30_sec"):
                item.pop(key, None)
    return items


@mcp.tool()
async def get_sleep_time_recommendations(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """Oura's recommended bedtime window per day."""
    return await _daily("sleep_time", start_date, end_date)


@mcp.tool()
async def get_workouts(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """Workouts: activity type, start/end, calories, distance, intensity, source."""
    return await _daily("workout", start_date, end_date)


@mcp.tool()
async def get_sessions(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """Guided/unguided sessions (meditation, breathing, rest) with heart rate and HRV."""
    return await _daily("session", start_date, end_date)


@mcp.tool()
async def get_tags(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """Tags the user logged in the Oura app (e.g. alcohol, caffeine, sick) with notes."""
    return await _daily("enhanced_tag", start_date, end_date)


@mcp.tool()
async def get_rest_mode_periods(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """Periods when Rest Mode was on (illness/recovery), with episodes."""
    return await _daily("rest_mode_period", start_date, end_date)


@mcp.tool()
async def get_heart_rate(
    start_datetime: str | None = None,
    end_datetime: str | None = None,
    summarize: bool = True,
) -> dict[str, Any]:
    """Heart rate samples (~every 5 min) between two ISO 8601 datetimes
    (default: last 24 hours). Range is limited to 30 days by Oura.
    With summarize=True (default) returns min/avg/max per source and per day
    instead of every sample; set summarize=False for raw samples."""
    end = datetime.fromisoformat(end_datetime) if end_datetime else datetime.now(timezone.utc)
    start = datetime.fromisoformat(start_datetime) if start_datetime else end - timedelta(hours=24)
    samples = await _collect(
        "heartrate",
        {"start_datetime": start.isoformat(), "end_datetime": end.isoformat()},
    )
    if not summarize:
        return {"count": len(samples), "samples": samples}

    def stats(values: list[int]) -> dict[str, Any]:
        return {
            "count": len(values),
            "min": min(values),
            "avg": round(sum(values) / len(values), 1),
            "max": max(values),
        }

    by_source: dict[str, list[int]] = {}
    by_day: dict[str, list[int]] = {}
    for s in samples:
        by_source.setdefault(s.get("source", "unknown"), []).append(s["bpm"])
        by_day.setdefault(s["timestamp"][:10], []).append(s["bpm"])
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "overall": stats([s["bpm"] for s in samples]) if samples else None,
        "by_source": {k: stats(v) for k, v in by_source.items()},
        "by_day": {k: stats(v) for k, v in sorted(by_day.items())},
    }


# --- Convenience -------------------------------------------------------------

@mcp.tool()
async def get_daily_overview(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    """One row per day combining the headline numbers: sleep, readiness and
    activity scores, steps, total sleep, HRV, resting/lowest heart rate,
    temperature deviation, stress. Good starting point for trend questions."""
    s, e = _date_range(start_date, end_date)
    params = {"start_date": s, "end_date": e}
    sleep_scores = await _collect("daily_sleep", params)
    readiness = await _collect("daily_readiness", params)
    activity = await _collect("daily_activity", params)
    stress = await _collect("daily_stress", params)
    periods = await _collect("sleep", params)

    days: dict[str, dict[str, Any]] = {}

    def row(day: str) -> dict[str, Any]:
        return days.setdefault(day, {"day": day})

    for d in sleep_scores:
        row(d["day"])["sleep_score"] = d.get("score")
    for d in readiness:
        r = row(d["day"])
        r["readiness_score"] = d.get("score")
        r["temperature_deviation_c"] = d.get("temperature_deviation")
    for d in activity:
        r = row(d["day"])
        r["activity_score"] = d.get("score")
        r["steps"] = d.get("steps")
        r["active_calories"] = d.get("active_calories")
    for d in stress:
        r = row(d["day"])
        r["stress_high_min"] = round((d.get("stress_high") or 0) / 60)
        r["recovery_high_min"] = round((d.get("recovery_high") or 0) / 60)
        r["stress_summary"] = d.get("day_summary")
    for p in periods:
        if p.get("type") != "long_sleep":
            continue
        r = row(p["day"])
        r["total_sleep_h"] = round((p.get("total_sleep_duration") or 0) / 3600, 2)
        r["bedtime_start"] = p.get("bedtime_start")
        r["bedtime_end"] = p.get("bedtime_end")
        r["average_hrv"] = p.get("average_hrv")
        r["lowest_heart_rate"] = p.get("lowest_heart_rate")
        r["average_heart_rate"] = p.get("average_heart_rate")

    return [days[k] for k in sorted(days)]


def main() -> None:
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "login":
        login(sys.argv[2:])
        return
    mcp.run()


if __name__ == "__main__":
    main()

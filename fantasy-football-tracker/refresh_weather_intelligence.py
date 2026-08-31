from __future__ import annotations

import csv
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
DB = ROOT / "data" / "fantasy_tracker.sqlite"

# Stadium-area coordinates. Exact street-level precision is unnecessary for forecast modeling;
# these are intentionally stable metro/stadium coordinates rather than a geocoding dependency.
COORDS = {
    "ARI": (33.5276, -112.2626), "ATL": (33.7554, -84.4008), "BAL": (39.2780, -76.6227),
    "BUF": (42.7738, -78.7870), "CAR": (35.2258, -80.8528), "CHI": (41.8623, -87.6167),
    "CIN": (39.0954, -84.5160), "CLE": (41.5061, -81.6995), "DAL": (32.7473, -97.0945),
    "DEN": (39.7439, -105.0201), "DET": (42.3400, -83.0456), "GB": (44.5013, -88.0622),
    "HOU": (29.6847, -95.4107), "IND": (39.7601, -86.1639), "JAX": (30.3239, -81.6373),
    "KC": (39.0489, -94.4839), "LV": (36.0909, -115.1833), "LAC": (33.9535, -118.3392),
    "LAR": (33.9535, -118.3392), "MIA": (25.9580, -80.2389), "MIN": (44.9736, -93.2575),
    "NE": (42.0909, -71.2643), "NO": (29.9511, -90.0812), "NYG": (40.8135, -74.0745),
    "NYJ": (40.8135, -74.0745), "PHI": (39.9008, -75.1675), "PIT": (40.4468, -80.0158),
    "SEA": (47.5952, -122.3316), "SF": (37.4030, -121.9700), "TB": (27.9759, -82.5033),
    "TEN": (36.1665, -86.7713), "WAS": (38.9076, -76.8645),
}
TEAM_FIX = {"JAC": "JAX", "WSH": "WAS", "LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR"}


def team_fix(v):
    x = str(v or "").strip().upper()
    return TEAM_FIX.get(x, x)


def read_csv(name: str) -> list[dict]:
    p = OUT / name
    if not p.exists(): return []
    with p.open("r", newline="", encoding="utf-8") as f: return list(csv.DictReader(f))


def write_csv(name: str, rows: list[dict]):
    if not rows: return
    fields, seen = [], set()
    for r in rows:
        for k in r:
            if k not in seen: seen.add(k); fields.append(k)
    with (OUT / name).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def replace_table(name: str, rows: list[dict]):
    con = sqlite3.connect(DB)
    try:
        con.execute(f'DROP TABLE IF EXISTS "{name}"')
        if rows:
            fs = list(rows[0]); defs = ", ".join(f'"{c}" TEXT' for c in fs); cols = ",".join(f'"{c}"' for c in fs); qs = ",".join("?" for _ in fs)
            con.execute(f'CREATE TABLE "{name}" ({defs})')
            con.executemany(f'INSERT INTO "{name}" ({cols}) VALUES ({qs})', [[None if r.get(c) is None else str(r.get(c)) for c in fs] for r in rows])
        con.commit()
    finally: con.close()


def fnum(v, default=None):
    try: return float(v)
    except (TypeError, ValueError): return default


def kickoff_utc(gameday: str, gametime: str) -> datetime:
    t = str(gametime or "13:00").strip()
    try: local = datetime.fromisoformat(f"{gameday}T{t}").replace(tzinfo=ZoneInfo("America/New_York"))
    except ValueError: local = datetime.fromisoformat(f"{gameday}T13:00").replace(tzinfo=ZoneInfo("America/New_York"))
    return local.astimezone(ZoneInfo("UTC"))


def get_forecast(lat: float, lon: float, day: str) -> dict:
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,precipitation_probability,precipitation,wind_speed_10m,wind_gusts_10m",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
        "timezone": "UTC", "start_date": day, "end_date": day,
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urlencode(params)
    req = Request(url, headers={"User-Agent": "blatzzy-fantasy-tracker/1.0"})
    with urlopen(req, timeout=30) as r: return json.load(r)


def weather_penalties(wind, gust, precip_prob, precip, temp, roof_factor=1.0):
    pass_pen = kick_pen = run_pen = 0.0
    notes = []
    if wind is not None:
        if wind >= 25: pass_pen -= 1.8; kick_pen -= 1.8; run_pen -= 0.25; notes.append(f"wind {wind:.0f} mph")
        elif wind >= 20: pass_pen -= 1.2; kick_pen -= 1.35; run_pen -= 0.15; notes.append(f"wind {wind:.0f} mph")
        elif wind >= 15: pass_pen -= 0.45; kick_pen -= 0.55; notes.append(f"wind {wind:.0f} mph")
    if gust is not None and gust >= 35:
        pass_pen -= 0.3; kick_pen -= 0.35; notes.append(f"gusts {gust:.0f} mph")
    if precip_prob is not None and precip_prob >= 70 and (precip or 0) >= 0.01:
        pass_pen -= 0.35; kick_pen -= 0.2; run_pen -= 0.1; notes.append(f"precip {precip_prob:.0f}%")
    if temp is not None and temp <= 20:
        pass_pen -= 0.2; kick_pen -= 0.15; run_pen -= 0.05; notes.append(f"{temp:.0f}F")
    elif temp is not None and temp >= 95:
        pass_pen -= 0.1; run_pen -= 0.1; notes.append(f"{temp:.0f}F heat")
    return round(pass_pen * roof_factor, 2), round(run_pen * roof_factor, 2), round(kick_pen * roof_factor, 2), " | ".join(notes)


def main():
    games = read_csv("week_games.csv")
    rows = []
    ts = datetime.now(ZoneInfo("UTC")).replace(microsecond=0).isoformat()
    for g in games:
        home = team_fix(g.get("home_team")); roof = str(g.get("roof") or "").lower(); day = str(g.get("gameday") or "")
        kickoff = kickoff_utc(day, str(g.get("gametime") or "13:00")) if day else None
        indoor = roof in {"dome", "closed"}
        roof_factor = 0.5 if roof == "retractable" else 1.0
        source = "indoor/no weather adjustment" if indoor else "Open-Meteo game-time forecast"
        temp = wind = gust = precip_prob = precip = None
        forecast_error = ""
        if not indoor and home in COORDS and kickoff:
            try:
                data = get_forecast(*COORDS[home], day)
                hourly = data.get("hourly") or {}; times = hourly.get("time") or []
                if times:
                    target = kickoff.replace(tzinfo=None)
                    idx = min(range(len(times)), key=lambda i: abs((datetime.fromisoformat(times[i]) - target).total_seconds()))
                    temp = fnum((hourly.get("temperature_2m") or [None] * len(times))[idx])
                    wind = fnum((hourly.get("wind_speed_10m") or [None] * len(times))[idx])
                    gust = fnum((hourly.get("wind_gusts_10m") or [None] * len(times))[idx])
                    precip_prob = fnum((hourly.get("precipitation_probability") or [None] * len(times))[idx])
                    precip = fnum((hourly.get("precipitation") or [None] * len(times))[idx])
            except Exception as e:
                forecast_error = str(e)[:180]
                source = "schedule fallback"
        if not indoor and temp is None: temp = fnum(g.get("temp"))
        if not indoor and wind is None: wind = fnum(g.get("wind"))
        ppass, prun, pkick, notes = (0.0, 0.0, 0.0, "indoor") if indoor else weather_penalties(wind, gust, precip_prob, precip, temp, roof_factor)
        severity = "INDOOR" if indoor else "HIGH" if min(ppass, pkick) <= -1.2 else "MODERATE" if min(ppass, pkick) <= -0.45 else "LOW"
        row = {
            "snapshot_utc": ts, "game_id": g.get("game_id"), "week": g.get("week"), "gameday": day, "gametime": g.get("gametime"),
            "kickoff_utc": kickoff.isoformat() if kickoff else None, "home_team": home, "away_team": team_fix(g.get("away_team")),
            "stadium": g.get("stadium"), "roof": roof, "forecast_source": source, "forecast_error": forecast_error,
            "temperature_f": temp, "wind_mph": wind, "gust_mph": gust, "precip_probability": precip_prob, "precip_inches": precip,
            "weather_severity": severity, "pass_weather_adjustment": ppass, "run_weather_adjustment": prun, "kick_weather_adjustment": pkick,
            "weather_notes": notes or "no material weather signal",
        }
        rows.append(row)
    write_csv("weather_intelligence.csv", rows); replace_table("weather_intelligence", rows)
    summary = {
        "snapshot_utc": ts, "games": len(rows), "forecasted_games": sum(1 for r in rows if r["forecast_source"] == "Open-Meteo game-time forecast"),
        "indoor_games": sum(1 for r in rows if r["weather_severity"] == "INDOOR"),
        "high_weather_games": [r["game_id"] for r in rows if r["weather_severity"] == "HIGH"],
        "note": "Game times are interpreted from the nflverse schedule as Eastern Time, converted to UTC, then matched to the nearest Open-Meteo forecast hour. Retractable-roof weather impact is halved until roof state is known.",
    }
    (ROOT / "data" / "weather_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    lines = ["# Week Weather Intelligence", "", summary["note"], ""]
    for r in rows:
        lines.append(f"- {r['away_team']} @ {r['home_team']} — {r['weather_severity']} — {r['weather_notes']} — pass {float(r['pass_weather_adjustment']):+.2f}, kick {float(r['kick_weather_adjustment']):+.2f}")
    (ROOT / "data" / "weather_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__": main()

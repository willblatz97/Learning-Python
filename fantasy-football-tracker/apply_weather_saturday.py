from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
DB = ROOT / "data" / "fantasy_tracker.sqlite"
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


def num(v, default=0.0):
    try: return float(v)
    except (TypeError, ValueError): return default


def main():
    scores = read_csv("player_week_scores.csv")
    games = read_csv("weather_intelligence.csv")
    by_team = {}
    for g in games:
        by_team[team_fix(g.get("home_team"))] = g
        by_team[team_fix(g.get("away_team"))] = g
    changed = 0
    for r in scores:
        team = team_fix(r.get("nfl_team")); g = by_team.get(team); base = num(r.get("lineup_score"))
        r["base_lineup_score_before_weather"] = round(base, 2); r["weather_points_applied"] = 0.0
        if not g: continue
        pos = str(r.get("position") or "")
        ppass = num(g.get("pass_weather_adjustment")); prun = num(g.get("run_weather_adjustment")); pkick = num(g.get("kick_weather_adjustment"))
        if pos in {"QB", "WR"}: applied = ppass
        elif pos == "TE": applied = ppass * 0.8
        elif pos == "RB": applied = prun + ppass * 0.12
        elif pos == "K": applied = pkick
        else: applied = 0.0
        applied = round(max(-1.8, min(0.15, applied)), 2)
        r["weather_points_applied"] = applied; r["weather_severity"] = g.get("weather_severity"); r["weather_notes"] = g.get("weather_notes")
        r["forecast_temp_f"] = g.get("temperature_f"); r["forecast_wind_mph"] = g.get("wind_mph"); r["forecast_gust_mph"] = g.get("gust_mph"); r["forecast_precip_probability"] = g.get("precip_probability"); r["forecast_source"] = g.get("forecast_source")
        if applied:
            r["lineup_score"] = round(max(0.0, base + applied), 2); r["score_source"] = str(r.get("score_source") or "") + " + game-time weather"; changed += 1
    write_csv("player_week_scores.csv", scores); replace_table("player_week_scores", scores)
    print(json.dumps({"weather_adjusted_player_scores": changed, "max_individual_penalty": -1.8, "note": "Weather is zero indoors and primarily wind-driven outdoors; precipitation and temperature are secondary modifiers."}, indent=2))


if __name__ == "__main__": main()

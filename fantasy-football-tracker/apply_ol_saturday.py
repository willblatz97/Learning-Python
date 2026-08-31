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
    with p.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


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
            fields = list(rows[0].keys()); defs = ", ".join(f'"{c}" TEXT' for c in fields); cols = ",".join(f'"{c}"' for c in fields); qs = ",".join("?" for _ in fields)
            con.execute(f'CREATE TABLE "{name}" ({defs})')
            con.executemany(f'INSERT INTO "{name}" ({cols}) VALUES ({qs})', [[None if r.get(c) is None else str(r.get(c)) for c in fields] for r in rows])
        con.commit()
    finally: con.close()


def num(v, default=0.0):
    try: return float(v)
    except (TypeError, ValueError): return default


def player_adjustment(pos: str, pass_adj: float, run_adj: float) -> float:
    # Team-level pass/run penalties are intentionally translated conservatively to individuals.
    if pos == "QB": raw = pass_adj * 0.75
    elif pos == "WR": raw = pass_adj * 0.40
    elif pos == "TE": raw = pass_adj * 0.32 + run_adj * 0.08
    elif pos == "RB": raw = run_adj * 0.62 + pass_adj * 0.10
    else: raw = 0.0
    return round(max(-1.5, min(0.25, raw)), 2)


def main():
    scores = read_csv("player_week_scores.csv")
    health = {team_fix(r.get("team")): r for r in read_csv("ol_health.csv")}
    changed = 0
    for r in scores:
        team = team_fix(r.get("nfl_team"))
        h = health.get(team)
        base = num(r.get("lineup_score"))
        r["base_lineup_score_before_ol"] = round(base, 2)
        r["ol_points_applied"] = 0.0
        if not h: continue
        pass_adj = num(h.get("pass_game_adjustment")); run_adj = num(h.get("run_game_adjustment"))
        applied = player_adjustment(str(r.get("position") or ""), pass_adj, run_adj)
        r["ol_health_score"] = h.get("ol_health_score")
        r["ol_grade"] = h.get("ol_grade")
        r["ol_concerns"] = h.get("concerns")
        r["team_pass_ol_adjustment"] = pass_adj
        r["team_run_ol_adjustment"] = run_adj
        r["ol_points_applied"] = applied
        if applied:
            r["lineup_score"] = round(max(0.0, base + applied), 2)
            r["score_source"] = str(r.get("score_source") or "") + " + OL health"
            changed += 1
    write_csv("player_week_scores.csv", scores); replace_table("player_week_scores", scores)
    print(json.dumps({"ol_adjusted_player_scores": changed, "max_individual_penalty": -1.5, "note": "OL health is a secondary projection modifier; individual adjustments are conservative and bounded."}, indent=2))


if __name__ == "__main__": main()

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
DB = ROOT / "data" / "fantasy_tracker.sqlite"


def read_csv(name: str) -> list[dict]:
    p = OUT / name
    if not p.exists():
        return []
    with p.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(name: str, rows: list[dict]):
    if not rows:
        return
    fields = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with (OUT / name).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def replace_table(name: str, rows: list[dict]):
    con = sqlite3.connect(DB)
    try:
        con.execute(f'DROP TABLE IF EXISTS "{name}"')
        if rows:
            fields = list(rows[0].keys())
            defs = ", ".join(f'"{c}" TEXT' for c in fields)
            cols = ",".join(f'"{c}"' for c in fields)
            qs = ",".join("?" for _ in fields)
            con.execute(f'CREATE TABLE "{name}" ({defs})')
            con.executemany(
                f'INSERT INTO "{name}" ({cols}) VALUES ({qs})',
                [[None if r.get(c) is None else str(r.get(c)) for c in fields] for r in rows],
            )
        con.commit()
    finally:
        con.close()


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def truthy(v) -> bool:
    return str(v).lower() in {"true", "1", "yes"}


def lineup_usage_points(raw_adjustment: float, position: str, games: int) -> float:
    """Translate the broader usage signal into a conservative weekly point adjustment.

    Waivers can react aggressively to role changes; start/sit projections should move less.
    One-game samples are intentionally damped, two-game samples are partial, and three-plus
    games can receive the full bounded weekly adjustment.
    """
    sample_factor = 1.0 if games >= 3 else 0.65 if games == 2 else 0.4 if games == 1 else 0.0
    pos_factor = 0.9 if position in {"RB", "WR", "TE"} else 0.45 if position == "QB" else 0.0
    return round(max(-2.75, min(2.75, raw_adjustment * 0.45 * sample_factor * pos_factor)), 2)


def main():
    scores = read_csv("player_week_scores.csv")
    usage_rows = read_csv("usage_trends.csv")
    usage = {str(r.get("sleeper_id")): r for r in usage_rows}

    adjusted = 0
    current_rows = 0
    for r in scores:
        pid = str(r.get("player_id") or "")
        u = usage.get(pid)
        base = num(r.get("lineup_score"))
        r["base_lineup_score_before_usage"] = round(base, 2)
        r["usage_points_applied"] = 0.0
        if not u:
            continue

        r["usage_season"] = u.get("usage_season")
        r["usage_signal"] = u.get("usage_signal")
        r["usage_latest_week"] = u.get("latest_week")
        r["usage_games_with_stats"] = u.get("games_with_stats")
        r["usage_games_with_snaps"] = u.get("games_with_snaps")
        r["latest_offense_snap_pct"] = u.get("latest_offense_snap_pct")
        r["last3_offense_snap_pct"] = u.get("last3_offense_snap_pct")
        r["season_offense_snap_pct"] = u.get("season_offense_snap_pct")
        r["last3_targets_pg"] = u.get("last3_targets_pg")
        r["last3_carries_pg"] = u.get("last3_carries_pg")
        r["last3_opportunities_pg"] = u.get("last3_opportunities_pg")
        r["usage_adjustment_raw"] = u.get("usage_adjustment")

        if not truthy(u.get("current_season_data")):
            # Preserve prior-year context in the table without changing a 2026 lineup score.
            continue

        current_rows += 1
        raw = num(u.get("usage_adjustment"))
        games = max(int(num(u.get("games_with_stats"))), int(num(u.get("games_with_snaps"))))
        applied = lineup_usage_points(raw, str(r.get("position") or ""), games)
        if applied == 0:
            continue

        r["usage_points_applied"] = applied
        r["lineup_score"] = round(max(0.0, base + applied), 2)
        r["score_source"] = str(r.get("score_source") or "") + " + current usage"
        adjusted += 1

    write_csv("player_week_scores.csv", scores)
    replace_table("player_week_scores", scores)
    print(json.dumps({
        "current_season_usage_rows_seen": current_rows,
        "usage_adjusted_player_scores": adjusted,
        "note": "Prior-season usage remains informational only; current-season role changes are sample-size damped and capped at +/-2.75 lineup points.",
    }, indent=2))


if __name__ == "__main__":
    main()

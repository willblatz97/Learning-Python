from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "fantasy_tracker.sqlite"
LEAGUE_ID = "1359546418284494848"
ROSTER_URL = "https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_2026.csv"
STAT_URL = lambda season: f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"


def fetch(url: str):
    try:
        req = Request(url, headers={"User-Agent": "blatzzy-fantasy-tracker/1.0"})
        with urlopen(req, timeout=90) as r:
            return r.read().decode("utf-8-sig", errors="replace")
    except HTTPError as e:
        if e.code == 404:
            return None
        raise


def rows(text):
    return list(csv.DictReader(io.StringIO(text))) if text else []


def num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def integer(v, d=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def avg(xs):
    return sum(xs) / len(xs) if xs else 0.0


def idp_group(p: dict) -> str | None:
    pos = str(p.get("position") or "").upper()
    try:
        fps = p.get("fantasy_positions") or []
        if isinstance(fps, str):
            fps = json.loads(fps)
    except Exception:
        fps = []
    vals = {pos, *(str(x).upper() for x in fps)}
    if vals & {"DL", "DE", "DT", "NT", "EDGE"}:
        return "DL"
    if vals & {"LB", "ILB", "OLB"}:
        return "LB"
    if vals & {"DB", "CB", "S", "FS", "SS"}:
        return "DB"
    return None


def stat(r: dict, *names):
    for n in names:
        if r.get(n) not in (None, ""):
            return num(r.get(n))
    return 0.0


def score_row(r: dict, s: dict) -> float:
    return (
        stat(r, "def_tackles_solo") * num(s.get("idp_tkl_solo"))
        + stat(r, "def_tackle_assists") * num(s.get("idp_tkl_ast"))
        + stat(r, "def_tackles_for_loss") * num(s.get("idp_tkl_loss"))
        + stat(r, "def_fumbles_forced") * num(s.get("idp_ff"))
        + stat(r, "def_fumble_recovery_opp", "fumble_recovery_opp") * num(s.get("idp_fum_rec"))
        + stat(r, "def_sacks") * num(s.get("idp_sack"))
        + stat(r, "def_qb_hits") * num(s.get("idp_qb_hit"))
        + stat(r, "def_interceptions") * num(s.get("idp_int"))
        + stat(r, "def_pass_defended") * num(s.get("idp_pass_def"))
        + stat(r, "def_tds") * num(s.get("idp_def_td"))
        + stat(r, "def_safety", "def_safeties") * num(s.get("idp_safe"))
    )


def write_csv(name: str, rs: list[dict]):
    p = OUT / name
    if not rs:
        p.write_text("", encoding="utf-8")
        return
    fields, seen = [], set()
    for r in rs:
        for k in r:
            if k not in seen:
                seen.add(k)
                fields.append(k)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rs)


def replace_table(name: str, rs: list[dict]):
    con = sqlite3.connect(DB)
    try:
        con.execute(f'DROP TABLE IF EXISTS "{name}"')
        if rs:
            fs = list(rs[0])
            cols_def = ", ".join(f'"{c}" TEXT' for c in fs)
            con.execute(f'CREATE TABLE "{name}" ({cols_def})')
            qs = ",".join("?" for _ in fs)
            cols = ",".join(f'"{c}"' for c in fs)
            con.executemany(
                f'INSERT INTO "{name}" ({cols}) VALUES ({qs})',
                [[None if r.get(c) is None else str(r.get(c)) for c in fs] for r in rs],
            )
        con.commit()
    finally:
        con.close()


def main():
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    league = json.loads((RAW / "leagues" / LEAGUE_ID / "league.json").read_text(encoding="utf-8"))
    scoring = league.get("scoring_settings") or {}
    sleeper = json.loads((RAW / "players_nfl.json").read_text(encoding="utf-8"))
    roster = rows(fetch(ROSTER_URL))
    gsis_to_sleeper = {
        str(r.get("gsis_id") or "").strip(): str(r.get("sleeper_id") or "").strip()
        for r in roster if r.get("gsis_id") and r.get("sleeper_id")
    }

    stats26 = rows(fetch(STAT_URL(2026)))
    current = any(str(r.get("season_type") or "REG").upper() == "REG" for r in stats26)
    season = 2026 if current else 2025
    stats = stats26 if current else rows(fetch(STAT_URL(2025)))

    by = defaultdict(list)
    for r in stats:
        if str(r.get("season_type") or "REG").upper() != "REG":
            continue
        sid = gsis_to_sleeper.get(str(r.get("player_id") or r.get("gsis_id") or "").strip())
        p = sleeper.get(str(sid), {}) if sid else {}
        group = idp_group(p)
        if sid and group:
            x = dict(r); x["league_points"] = score_row(r, scoring); x["group"] = group
            by[sid].append(x)

    out = []
    for sid, rs in by.items():
        rs.sort(key=lambda r: integer(r.get("week")))
        p = sleeper.get(str(sid), {})
        pts = [num(r.get("league_points")) for r in rs]
        solos = [stat(r, "def_tackles_solo") for r in rs]
        assists = [stat(r, "def_tackle_assists") for r in rs]
        sacks = [stat(r, "def_sacks") for r in rs]
        ints = [stat(r, "def_interceptions") for r in rs]
        pd = [stat(r, "def_pass_defended") for r in rs]
        group = rs[-1]["group"]
        baseline = avg(pts[-5:]) * 0.6 + avg(pts) * 0.4 if pts else 0.0
        inj = str(p.get("injury_status") or "").lower()
        if any(x in inj for x in ["out", "ir", "pup"]): baseline = 0.0
        elif "doubt" in inj: baseline *= 0.35
        elif "question" in inj: baseline *= 0.85
        out.append({
            "snapshot_utc": ts, "sleeper_id": sid, "player": p.get("full_name"), "nfl_team": p.get("team"),
            "idp_position": group, "source_season": season, "current_season_data": str(current), "games": len(rs),
            "season_points_pg": round(avg(pts), 2), "last5_points_pg": round(avg(pts[-5:]), 2),
            "idp_projection_proxy": round(baseline, 2), "solo_tackles_pg": round(avg(solos), 2),
            "assists_pg": round(avg(assists), 2), "sacks_pg": round(avg(sacks), 2),
            "interceptions_pg": round(avg(ints), 2), "passes_defended_pg": round(avg(pd), 2),
            "injury_status": p.get("injury_status"), "depth_chart_order": p.get("depth_chart_order"),
        })
    out.sort(key=lambda r: (r["idp_position"], -num(r["idp_projection_proxy"])))
    write_csv("idp_values.csv", out)
    replace_table("idp_values", out)
    summary = {
        "snapshot_utc": ts, "league_id": LEAGUE_ID, "source_season": season, "current_season_data": current,
        "players": len(out), "lineup_slots": {"DL": 1, "LB": 1, "DB": 1},
        "scoring": {k: scoring.get(k) for k in ["idp_tkl_solo","idp_tkl_ast","idp_tkl_loss","idp_ff","idp_fum_rec","idp_sack","idp_qb_hit","idp_int","idp_pass_def","idp_def_td","idp_safe"]},
        "note": "IDP values use this league's exact Sleeper scoring. Before 2026 games exist, 2025 league-scored production is a labeled baseline proxy; the feed automatically switches to 2026 regular-season data after kickoff."
    }
    (ROOT / "data" / "idp_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    lines = ["# League Is Rigged V2 — IDP Board", "", summary["note"], ""]
    for group in ["DL", "LB", "DB"]:
        lines.append(f"## {group}")
        for r in [x for x in out if x["idp_position"] == group][:12]:
            lines.append(f"- {r['player']} ({r['nfl_team']}) — {r['idp_projection_proxy']} proxy pts/g; tackles {r['solo_tackles_pg']} solo + {r['assists_pg']} ast; sacks {r['sacks_pg']}; PD {r['passes_defended_pg']}")
        lines.append("")
    (ROOT / "data" / "idp_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

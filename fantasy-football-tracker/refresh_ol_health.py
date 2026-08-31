from __future__ import annotations

import csv
import gzip
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
SEASON = 2026
DEPTH_URL = f"https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_{SEASON}.csv.gz"
ROSTER_URL = f"https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_{SEASON}.csv"
INJURY_URL = f"https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{SEASON}.csv"
TEAM_FIX = {"JAC": "JAX", "WSH": "WAS", "LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR"}
OL_SLOTS = ("LT", "LG", "C", "RG", "RT")


def team_fix(v):
    x = str(v or "").strip().upper()
    return TEAM_FIX.get(x, x)


def fetch_bytes(url: str) -> bytes | None:
    try:
        req = Request(url, headers={"User-Agent": "blatzzy-fantasy-tracker/1.0"})
        with urlopen(req, timeout=90) as r:
            return r.read()
    except HTTPError as e:
        if e.code == 404:
            return None
        raise


def rows_url(url: str, gz=False) -> list[dict]:
    raw = fetch_bytes(url)
    if not raw:
        return []
    if gz:
        raw = gzip.decompress(raw)
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))))


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_csv(name: str, rows: list[dict]):
    p = OUT / name
    if not rows:
        p.write_text("", encoding="utf-8")
        return
    fields, seen = [], set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k); fields.append(k)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


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
            con.executemany(f'INSERT INTO "{name}" ({cols}) VALUES ({qs})', [[None if r.get(c) is None else str(r.get(c)) for c in fields] for r in rows])
        con.commit()
    finally:
        con.close()


def integer(v, default=99):
    try: return int(float(v))
    except (TypeError, ValueError): return default


def ol_slot(row: dict) -> str | None:
    abb = str(row.get("pos_abb") or "").strip().upper().replace(" ", "")
    name = str(row.get("pos_name") or "").strip().upper()
    slot = str(row.get("pos_slot") or "").strip().upper().replace(" ", "")
    for value in (abb, slot):
        if value in OL_SLOTS:
            return value
    aliases = {
        "LEFT TACKLE": "LT", "LEFT GUARD": "LG", "CENTER": "C",
        "RIGHT GUARD": "RG", "RIGHT TACKLE": "RT",
    }
    return aliases.get(name)


def status_bucket(player: dict, official: dict | None) -> tuple[str, float, str]:
    sleeper = str(player.get("injury_status") or "").strip().lower()
    status = str(player.get("status") or "").strip().lower()
    practice = str(player.get("practice_participation") or "").strip().lower()
    official_status = str((official or {}).get("report_status") or (official or {}).get("game_status") or "").strip().lower()
    official_practice = str((official or {}).get("practice_status") or (official or {}).get("practice_participation") or "").strip().lower()
    blob = " | ".join(x for x in [sleeper, status, practice, official_status, official_practice] if x)
    if any(x in blob for x in ["injured reserve", "reserve/injured", "ir", "pup", "physically unable", "out"]):
        return "UNAVAILABLE", 1.0, blob
    if "doubt" in blob:
        return "DOUBTFUL", 0.75, blob
    if any(x in blob for x in ["question", "did not participate", "dnp"]):
        return "AT RISK", 0.45, blob
    if any(x in blob for x in ["limited", "lp"]):
        return "LIMITED", 0.25, blob
    return "HEALTHY", 0.0, blob


def main():
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    players = read_json(RAW / "players_nfl.json")
    depth = rows_url(DEPTH_URL, gz=True)
    roster = rows_url(ROSTER_URL)
    injuries = rows_url(INJURY_URL)

    gsis_to_sleeper, espn_to_sleeper = {}, {}
    for r in roster:
        sid = str(r.get("sleeper_id") or "").strip()
        if not sid or sid in {"NA", "None"}: continue
        gsis = str(r.get("gsis_id") or "").strip(); espn = str(r.get("espn_id") or "").strip()
        if gsis and gsis not in {"NA", "None"}: gsis_to_sleeper[gsis] = sid
        if espn and espn not in {"NA", "None"}: espn_to_sleeper[espn] = sid

    # Optional official weekly report: absent before the 2026 report pipeline opens.
    official = {}
    for r in injuries:
        gsis = str(r.get("gsis_id") or r.get("player_id") or "").strip()
        if not gsis: continue
        week = integer(r.get("week"), 0)
        old = official.get(gsis)
        if old is None or week >= integer(old.get("week"), 0):
            official[gsis] = r

    max_dt = {}
    for r in depth:
        team = team_fix(r.get("team")); dt = str(r.get("dt") or "")
        if team and (team not in max_dt or dt > max_dt[team]): max_dt[team] = dt
    latest = [r for r in depth if str(r.get("dt") or "") == max_dt.get(team_fix(r.get("team")))]

    candidates = defaultdict(list)
    for r in latest:
        slot = ol_slot(r)
        if not slot: continue
        team = team_fix(r.get("team"))
        sid = gsis_to_sleeper.get(str(r.get("gsis_id") or "").strip()) or espn_to_sleeper.get(str(r.get("espn_id") or "").strip())
        candidates[(team, slot)].append((integer(r.get("pos_rank")), sid, r))

    detailed, teams = [], []
    all_teams = sorted({team_fix(r.get("team")) for r in latest if team_fix(r.get("team"))})
    for team in all_teams:
        slot_info = {}
        total_risk = 0.0
        edge_risk = 0.0
        interior_risk = 0.0
        unavailable = at_risk = healthy = 0
        for slot in OL_SLOTS:
            options = sorted(candidates.get((team, slot), []), key=lambda x: x[0])
            if options:
                rank, sid, row = options[0]
                p = players.get(str(sid), {}) if sid else {}
                gsis = str(row.get("gsis_id") or "").strip()
                bucket, risk, detail = status_bucket(p, official.get(gsis))
                name = p.get("full_name") or row.get("player_name")
                # If source cannot map a listed starter, treat it as uncertainty, not an injury.
                if not sid:
                    risk = max(risk, 0.15); bucket = "UNMAPPED" if bucket == "HEALTHY" else bucket
            else:
                rank, sid, row = None, None, {}
                p = {}; name = None; bucket, risk, detail = "MISSING", 0.35, "no explicit starter in latest depth chart"

            weight = 1.2 if slot in {"LT", "RT"} else 1.0
            weighted = risk * weight
            total_risk += weighted
            if slot in {"LT", "RT"}: edge_risk += weighted
            else: interior_risk += weighted
            if risk >= 0.75: unavailable += 1
            elif risk > 0: at_risk += 1
            else: healthy += 1
            slot_info[slot] = {"name": name, "sid": sid, "bucket": bucket, "risk": risk}
            detailed.append({
                "snapshot_utc": ts, "source_dt": max_dt.get(team), "team": team, "slot": slot,
                "starter": name, "sleeper_id": sid, "depth_rank": rank, "health_bucket": bucket,
                "risk_weight": round(risk, 2), "status_detail": detail,
                "official_injury_report_available": str(bool(injuries)),
            })

        # 5 positions, with tackles carrying 20% extra leverage: max weighted risk 5.4.
        health_score = round(max(0.0, 100.0 * (1.0 - total_risk / 5.4)), 1)
        pass_penalty = round(max(-2.0, -(edge_risk * 0.65 + interior_risk * 0.25)), 2)
        run_penalty = round(max(-1.6, -(interior_risk * 0.45 + edge_risk * 0.2)), 2)
        if health_score >= 90: grade = "HEALTHY"
        elif health_score >= 75: grade = "MINOR CONCERN"
        elif health_score >= 55: grade = "DEGRADED"
        else: grade = "MAJOR CONCERN"
        concern_names = [f"{s} {slot_info[s]['name'] or '?'} ({slot_info[s]['bucket']})" for s in OL_SLOTS if slot_info[s]["risk"] > 0]
        teams.append({
            "snapshot_utc": ts, "source_dt": max_dt.get(team), "team": team,
            "ol_health_score": health_score, "ol_grade": grade, "healthy_starters": healthy,
            "at_risk_starters": at_risk, "unavailable_starters": unavailable,
            "pass_game_adjustment": pass_penalty, "run_game_adjustment": run_penalty,
            "lt": slot_info["LT"]["name"], "lg": slot_info["LG"]["name"], "c": slot_info["C"]["name"],
            "rg": slot_info["RG"]["name"], "rt": slot_info["RT"]["name"],
            "concerns": " | ".join(concern_names) if concern_names else "none",
            "official_injury_report_available": str(bool(injuries)),
        })

    teams.sort(key=lambda r: (float(r["ol_health_score"]), r["team"]))
    write_csv("ol_starters.csv", detailed); write_csv("ol_health.csv", teams)
    replace_table("ol_starters", detailed); replace_table("ol_health", teams)
    summary = {
        "snapshot_utc": ts, "teams": len(teams), "depth_source": DEPTH_URL,
        "official_injury_source": INJURY_URL if injuries else None,
        "official_injury_report_available": bool(injuries),
        "major_concern_teams": [r["team"] for r in teams if r["ol_grade"] == "MAJOR CONCERN"],
        "degraded_teams": [r["team"] for r in teams if r["ol_grade"] == "DEGRADED"],
        "note": "Current depth-chart starters plus Sleeper injury designations are always available. Official 2026 injury-report data is automatically layered in when the nflverse season asset appears.",
    }
    (ROOT / "data" / "ol_health_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    lines = ["# Offensive Line Health", "", summary["note"], "", "## Lowest current health scores"]
    for r in teams[:12]:
        lines.append(f"- {r['team']} — {r['ol_health_score']} {r['ol_grade']} — pass {float(r['pass_game_adjustment']):+.2f}, run {float(r['run_game_adjustment']):+.2f} — {r['concerns']}")
    (ROOT / "data" / "ol_health_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

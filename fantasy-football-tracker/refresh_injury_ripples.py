from __future__ import annotations

import csv
import gzip
import io
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "fantasy_tracker.sqlite"
SEASON = 2026
DEPTH_URL = f"https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_{SEASON}.csv.gz"
ROSTER_URL = f"https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_{SEASON}.csv"
TEAM_FIX = {"JAC": "JAX", "WSH": "WAS", "LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR"}


def team_fix(v):
    x = str(v or "").strip().upper()
    return TEAM_FIX.get(x, x)


def fetch_rows(url: str, gz=False):
    req = Request(url, headers={"User-Agent": "blatzzy-fantasy-tracker/1.0"})
    with urlopen(req, timeout=90) as r:
        raw = r.read()
    if gz:
        raw = gzip.decompress(raw)
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))))


def read_csv(name: str):
    p = OUT / name
    if not p.exists(): return []
    with p.open("r", newline="", encoding="utf-8") as f: return list(csv.DictReader(f))


def write_csv(name: str, rows: list[dict]):
    p = OUT / name
    if not rows:
        p.write_text("", encoding="utf-8"); return
    fields, seen = [], set()
    for r in rows:
        for k in r:
            if k not in seen: seen.add(k); fields.append(k)
    with p.open("w", newline="", encoding="utf-8") as f:
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


def norm_name(v):
    return "".join(ch.lower() for ch in str(v or "") if ch.isalnum())


def integer(v, default=99):
    try: return int(float(v))
    except (TypeError, ValueError): return default


def risk_bucket(p: dict) -> tuple[float, str]:
    blob = " | ".join(str(p.get(k) or "").lower() for k in ["injury_status", "status", "practice_participation", "practice_description"])
    if any(x in blob for x in ["injured reserve", "reserve/injured", "pup", "physically unable", "out", "inactive"]): return 1.0, "OUT/IR"
    if "doubt" in blob: return 0.8, "DOUBTFUL"
    if "question" in blob or "did not" in blob or "dnp" in blob: return 0.45, "QUESTIONABLE/DNP"
    if "limited" in blob: return 0.25, "LIMITED"
    return 0.0, "HEALTHY"


def role(row: dict) -> str:
    abb = str(row.get("pos_abb") or "").upper().strip()
    name = str(row.get("pos_name") or "").upper().strip()
    grp = str(row.get("pos_grp") or "").upper().strip()
    blob = f"{abb} {name} {grp}"
    if abb in {"QB", "RB", "WR", "TE", "CB", "S", "FS", "SS", "LB", "ILB", "OLB", "DE", "DT", "NT", "EDGE"}: return abb
    if "CORNER" in blob: return "CB"
    if "SAFETY" in blob: return "S"
    if "LINEBACK" in blob: return "LB"
    if "DEFENSIVE END" in blob or "EDGE" in blob: return "EDGE"
    if "DEFENSIVE TACKLE" in blob or "NOSE TACKLE" in blob: return "DT"
    return abb


def main():
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    players = json.loads((RAW / "players_nfl.json").read_text(encoding="utf-8"))
    depth = fetch_rows(DEPTH_URL, gz=True)
    roster = fetch_rows(ROSTER_URL)
    team_context = {team_fix(r.get("team")): r for r in read_csv("team_week_context.csv")}

    gsis_to_sid, espn_to_sid = {}, {}
    for r in roster:
        sid = str(r.get("sleeper_id") or "").strip()
        if not sid or sid in {"NA", "None"}: continue
        gsis = str(r.get("gsis_id") or "").strip(); espn = str(r.get("espn_id") or "").strip()
        if gsis and gsis not in {"NA", "None"}: gsis_to_sid[gsis] = sid
        if espn and espn not in {"NA", "None"}: espn_to_sid[espn] = sid

    name_team = {}
    for sid, p in players.items():
        t = team_fix(p.get("team")); n = norm_name(p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}")
        if t and n: name_team[(t, n)] = str(sid)

    max_dt = {}
    for r in depth:
        t = team_fix(r.get("team")); dt = str(r.get("dt") or "")
        if t and (t not in max_dt or dt > max_dt[t]): max_dt[t] = dt
    latest = [r for r in depth if str(r.get("dt") or "") == max_dt.get(team_fix(r.get("team")))]

    mapped = []
    for r in latest:
        t = team_fix(r.get("team")); sid = gsis_to_sid.get(str(r.get("gsis_id") or "").strip()) or espn_to_sid.get(str(r.get("espn_id") or "").strip())
        if not sid: sid = name_team.get((t, norm_name(r.get("player_name"))))
        rr = dict(r); rr["team_clean"] = t; rr["sid"] = sid; rr["role"] = role(r); rr["rank"] = integer(r.get("pos_rank")); mapped.append(rr)

    by_team_role = defaultdict(list)
    for r in mapped:
        if r["sid"] and r["role"]:
            by_team_role[(r["team_clean"], r["role"])].append(r)
    for k in by_team_role: by_team_role[k].sort(key=lambda x: x["rank"])

    def nth(team, roles, n=0):
        pool = []
        for rr in roles: pool += by_team_role.get((team, rr), [])
        pool.sort(key=lambda x: x["rank"])
        return pool[n] if len(pool) > n else None

    ripples = []
    def add(target, source, points, reason, side, confidence="MEDIUM"):
        if not target or not source or not target.get("sid") or abs(points) < 0.01: return
        tp = players.get(str(target["sid"]), {}); sp = players.get(str(source["sid"]), {})
        ripples.append({
            "snapshot_utc": ts, "target_sleeper_id": target["sid"], "target_player": tp.get("full_name") or target.get("player_name"),
            "target_team": target["team_clean"], "target_position": target["role"], "source_sleeper_id": source["sid"],
            "source_player": sp.get("full_name") or source.get("player_name"), "source_team": source["team_clean"], "source_position": source["role"],
            "ripple_points": round(points, 2), "side": side, "confidence": confidence, "reason": reason,
        })

    teams = sorted(set(t for t, _ in by_team_role))
    for team in teams:
        opp = team_fix((team_context.get(team) or {}).get("opponent"))
        # Offensive injuries redistribute opportunity inside the same offense.
        for rname in ["QB", "RB", "WR", "TE"]:
            starter = nth(team, [rname], 0)
            if not starter: continue
            p = players.get(str(starter["sid"]), {}); risk, label = risk_bucket(p)
            if risk <= 0: continue
            if rname == "RB":
                add(nth(team,["RB"],1), starter, 1.55*risk, f"RB1 {label}: backfield opportunity moves to RB2", "OFFENSE", "HIGH")
                add(nth(team,["RB"],2), starter, 0.45*risk, f"RB1 {label}: secondary backfield opportunity", "OFFENSE")
            elif rname == "WR":
                add(nth(team,["WR"],1), starter, 1.10*risk, f"WR1 {label}: target concentration rises for next WR", "OFFENSE", "HIGH")
                add(nth(team,["WR"],2), starter, 0.55*risk, f"WR1 {label}: WR3 role/targets can expand", "OFFENSE")
                add(nth(team,["TE"],0), starter, 0.50*risk, f"WR1 {label}: TE target share can rise", "OFFENSE")
            elif rname == "TE":
                add(nth(team,["TE"],1), starter, 0.85*risk, f"TE1 {label}: TE2 route/target opportunity rises", "OFFENSE", "HIGH")
                add(nth(team,["WR"],0), starter, 0.25*risk, f"TE1 {label}: WR1 target share can rise", "OFFENSE")
            elif rname == "QB":
                add(nth(team,["QB"],1), starter, 1.20*risk, f"QB1 {label}: QB2 becomes relevant", "OFFENSE", "HIGH")
                for target in [nth(team,["WR"],0), nth(team,["WR"],1), nth(team,["TE"],0)]:
                    add(target, starter, -0.45*risk, f"QB1 {label}: pass-catcher efficiency risk with backup QB", "OFFENSE")

        if not opp: continue
        # Defensive injuries alter opponent fantasy matchups. CB effects are projected coverage leverage, not confirmed shadow assignments.
        defense_groups = [(["CB"], "CB"), (["S","FS","SS"], "S"), (["LB","ILB","OLB"], "LB"), (["EDGE","DE"], "EDGE"), (["DT","NT"], "DT")]
        for roles, bucket in defense_groups:
            src = nth(team, roles, 0)
            if not src: continue
            p = players.get(str(src["sid"]), {}); risk, label = risk_bucket(p)
            if risk <= 0: continue
            if bucket == "CB":
                add(nth(opp,["WR"],0), src, 1.00*risk, f"Opponent CB1 {label}: projected WR1 coverage matchup improves (not a confirmed shadow assignment)", "DEFENSE", "MEDIUM")
                add(nth(opp,["WR"],1), src, 0.40*risk, f"Opponent CB1 {label}: secondary WR matchup improves", "DEFENSE")
            elif bucket == "S":
                add(nth(opp,["WR"],0), src, 0.40*risk, f"Starting safety {label}: downfield coverage environment improves", "DEFENSE")
                add(nth(opp,["TE"],0), src, 0.50*risk, f"Starting safety {label}: TE middle/deep matchup improves", "DEFENSE")
            elif bucket == "LB":
                add(nth(opp,["TE"],0), src, 0.55*risk, f"Starting linebacker {label}: TE coverage matchup improves", "DEFENSE")
                add(nth(opp,["RB"],0), src, 0.35*risk, f"Starting linebacker {label}: RB run/checkdown environment improves", "DEFENSE")
            elif bucket == "EDGE":
                add(nth(opp,["QB"],0), src, 0.45*risk, f"Top edge rusher {label}: opponent QB pressure environment improves", "DEFENSE")
                add(nth(opp,["WR"],0), src, 0.15*risk, f"Top edge rusher {label}: more time can help primary WR routes develop", "DEFENSE", "LOW")
            elif bucket == "DT":
                add(nth(opp,["RB"],0), src, 0.45*risk, f"Starting interior DL {label}: opponent rushing matchup improves", "DEFENSE")

    # Aggregate multiple simultaneous ripple effects by player and cap them conservatively.
    agg = defaultdict(lambda: {"points": 0.0, "reasons": [], "high": False})
    for r in ripples:
        a = agg[str(r["target_sleeper_id"])]; a["points"] += float(r["ripple_points"]); a["reasons"].append(r["reason"]); a["high"] = a["high"] or r["confidence"] == "HIGH"
    player_rows = []
    for sid, a in agg.items():
        p = players.get(sid, {}); pts = round(max(-2.25, min(2.25, a["points"])), 2)
        player_rows.append({"snapshot_utc": ts, "sleeper_id": sid, "player": p.get("full_name"), "team": team_fix(p.get("team")), "position": p.get("position"), "injury_ripple_points": pts, "confidence": "HIGH" if a["high"] else "MEDIUM", "reasons": " | ".join(a["reasons"])})
    player_rows.sort(key=lambda r: (-float(r["injury_ripple_points"]), r.get("player") or ""))
    write_csv("injury_ripples.csv", ripples); write_csv("injury_ripple_players.csv", player_rows)
    replace_table("injury_ripples", ripples); replace_table("injury_ripple_players", player_rows)
    summary = {"snapshot_utc": ts, "raw_ripple_events": len(ripples), "players_affected": len(player_rows), "positive_players": sum(float(r["injury_ripple_points"])>0 for r in player_rows), "negative_players": sum(float(r["injury_ripple_points"])<0 for r in player_rows), "max_player_adjustment": 2.25, "note": "Defensive coverage effects use current depth-chart roles. CB1-to-WR1 is a projected matchup leverage signal unless a confirmed shadow assignment is available later."}
    (ROOT/"data"/"injury_ripple_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    lines = ["# Injury Ripple Intelligence", "", summary["note"], "", "## Biggest current positive ripples"]
    for r in player_rows[:15]:
        if float(r["injury_ripple_points"]) <= 0: break
        lines.append(f"- {r['player']} ({r['team']} {r['position']}) {float(r['injury_ripple_points']):+.2f} — {r['reasons']}")
    (ROOT/"data"/"injury_ripple_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__": main()

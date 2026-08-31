from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
DB = ROOT / "data" / "fantasy_tracker.sqlite"
CONFIG = ROOT / "config.json"


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
        for k in row:
            if k not in seen:
                seen.add(k)
                fields.append(k)
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
            cols = ", ".join(f'"{c}" TEXT' for c in fields)
            con.execute(f'CREATE TABLE "{name}" ({cols})')
            qs = ",".join("?" for _ in fields)
            names = ",".join(f'"{c}"' for c in fields)
            con.executemany(
                f'INSERT INTO "{name}" ({names}) VALUES ({qs})',
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


def inum(v, default=99):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    labels = cfg.get("league_labels", {})
    leagues = {str(r.get("league_id")): r for r in read_csv("leagues.csv")}
    availability = {str(r.get("player_id")): r for r in read_csv("availability_matrix.csv")}
    roles = read_csv("return_roles.csv")
    boosts = read_csv("league_return_boosts.csv")

    role_by_pid = {}
    for r in roles:
        pid = str(r.get("sleeper_id") or "")
        av = availability.get(pid, {})
        pos = str(av.get("position") or r.get("position") or "")
        order = inum(av.get("depth_chart_order"), 99)
        fantasy_offense = pos in {"QB", "RB", "WR", "TE"}
        # Sleeper depth-chart data is the operational fallback when ESPN's same-player
        # offensive row does not survive the nflverse depth-chart join.
        if fantasy_offense and order <= 3:
            r["offensive_depth_position"] = av.get("depth_chart_position") or pos
            r["offensive_depth_rank"] = order
            r["dual_role_offense_return"] = "True"
            r["offensive_role_source"] = "Sleeper depth chart"
        elif str(r.get("offensive_depth_position") or ""):
            r["dual_role_offense_return"] = "True"
            r["offensive_role_source"] = "nflverse depth chart"
        else:
            r["dual_role_offense_return"] = "False"
            r["offensive_role_source"] = "none detected"
        r["injury_status"] = av.get("injury_status")
        r["practice_participation"] = av.get("practice_participation")
        role_by_pid[pid] = r

    roles.sort(key=lambda r: (
        -int(str(r.get("dual_role_offense_return")).lower() == "true"),
        -(1 if str(r.get("kr_rank")) == "1" or str(r.get("pr_rank")) == "1" else 0),
        -(num(r.get("projected_kr_yards_per_game")) + num(r.get("projected_pr_yards_per_game"))),
        num(r.get("redraft_ecr"), 9999),
        str(r.get("player") or ""),
    ))

    for b in boosts:
        r = role_by_pid.get(str(b.get("sleeper_id") or ""), {})
        b["dual_role_offense_return"] = r.get("dual_role_offense_return", "False")
        b["offensive_depth_rank"] = r.get("offensive_depth_rank")
        b["offensive_depth_position"] = r.get("offensive_depth_position")
        b["offensive_role_source"] = r.get("offensive_role_source")
        b["injury_status"] = r.get("injury_status")
        b["practice_participation"] = r.get("practice_participation")

    boosts.sort(key=lambda r: (
        str(r.get("league_id")),
        -num(r.get("return_projection_points")),
        -int(str(r.get("dual_role_offense_return")).lower() == "true"),
        str(r.get("player") or ""),
    ))

    write_csv("return_roles.csv", roles)
    write_csv("league_return_boosts.csv", boosts)
    replace_table("return_roles", roles)
    replace_table("league_return_boosts", boosts)

    scoring = read_csv("league_return_scoring.csv")
    lines = ["# Returner Intelligence", "", "Current KR/PR role + 2025 return baseline. Dual-role status uses Sleeper offensive depth as a fallback.", "", "## League return scoring"]
    for r in scoring:
        lines.append(f"- {r['league']}: KR {r['kr_points_per_yard']} pts/yd; PR {r['pr_points_per_yard']} pts/yd; return TD {r['individual_return_td_points']} pts")

    lines += ["", "## Primary returners with offensive role"]
    dual_primary = [r for r in roles if r.get("dual_role_offense_return") == "True" and (str(r.get("kr_rank")) == "1" or str(r.get("pr_rank")) == "1")]
    for r in dual_primary[:35]:
        yds = num(r.get("projected_kr_yards_per_game")) + num(r.get("projected_pr_yards_per_game"))
        own = f"; yours: {r['my_leagues']}" if r.get("my_leagues") else ""
        free = f"; FA: {r['free_agent_leagues']}" if r.get("free_agent_leagues") else ""
        lines.append(f"- {r['player']} ({r['position']} {r['nfl_team']}) — {r['return_roles']}; offensive depth {r.get('offensive_depth_position') or r['position']} {r.get('offensive_depth_rank')}; proj return yds {yds:.1f}/g; {r['confidence']} confidence{own}{free}")

    lines += ["", "## Chopped / return-yard league value"]
    return_lids = {str(r.get("league_id")) for r in scoring if num(r.get("kr_points_per_yard")) or num(r.get("pr_points_per_yard"))}
    for lid in sorted(return_lids):
        label = labels.get(lid, leagues.get(lid, {}).get("name", lid))
        lines.append(f"### {label}")
        rows = [r for r in boosts if str(r.get("league_id")) == lid and num(r.get("return_projection_points")) > 0]
        for r in rows[:20]:
            dual = " + OFF" if r.get("dual_role_offense_return") == "True" else ""
            lines.append(f"- {r['player']} — +{r['return_projection_points']} return pts/g ({r['return_roles']}{dual}, {r['confidence']}) — {r.get('roster_state')}")
        lines.append("")

    (ROOT / "data" / "returner_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"returners": len(roles), "dual_role_returners": sum(r.get('dual_role_offense_return') == 'True' for r in roles), "return_boost_rows": len(boosts)}, indent=2))


if __name__ == "__main__":
    main()

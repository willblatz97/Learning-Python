from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "fantasy_tracker.sqlite"
SUMMARY = ROOT / "data" / "summary.json"


def read_csv(name: str) -> list[dict]:
    path = OUT / name
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(name: str, rows: list[dict]):
    if not rows:
        return
    fields: list[str] = []
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


def truthy(value) -> bool:
    return str(value).lower() in {"true", "1", "yes"}


def load_players() -> dict:
    path = RAW / "players_nfl.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def slot_label(row: dict) -> str:
    if truthy(row.get("starter")):
        return "START"
    if truthy(row.get("reserve")):
        return "IR"
    if truthy(row.get("taxi")):
        return "TAXI"
    return "BENCH"


def fantasy_relevant(player: dict) -> bool:
    positions = set(player.get("fantasy_positions") or [])
    if player.get("position"):
        positions.add(player["position"])
    wanted = {"QB", "RB", "WR", "TE", "K", "DEF", "DL", "LB", "DB", "EDGE"}
    return bool(positions & wanted)


def main():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    league_ids = [str(x) for x in cfg["league_ids"]]
    labels = cfg.get("league_labels", {})
    league_names = summary.get("league_names", {})
    current_week = int((summary.get("nfl_state") or {}).get("week") or 1)

    ownership = read_csv("ownership.csv")
    rosters = read_csv("rosters.csv")
    matchups = read_csv("matchups.csv")
    players = load_players()

    my_roster_by_league = {
        str(r["league_id"]): str(r["roster_id"])
        for r in rosters if truthy(r.get("is_my_roster"))
    }

    # Current-week opponent roster IDs. Normal H2H resolves to one roster;
    # elimination/special formats can resolve to zero or multiple and are retained as a list.
    matchup_by_league_roster: dict[tuple[str, str], str] = {}
    rosters_by_matchup: dict[tuple[str, str], list[str]] = defaultdict(list)
    for m in matchups:
        if int(m.get("week") or 0) != current_week:
            continue
        lid, rid = str(m["league_id"]), str(m["roster_id"])
        mid = str(m.get("matchup_id") or "")
        matchup_by_league_roster[(lid, rid)] = mid
        if mid:
            rosters_by_matchup[(lid, mid)].append(rid)

    opponent_rosters: dict[str, set[str]] = {}
    for lid, my_rid in my_roster_by_league.items():
        mid = matchup_by_league_roster.get((lid, my_rid), "")
        opponent_rosters[lid] = set(rosters_by_matchup.get((lid, mid), [])) - {my_rid} if mid else set()

    # Fast lookup: ownership status of every rostered player in every league.
    owner_lookup: dict[tuple[str, str], dict] = {}
    by_player: dict[str, list[dict]] = defaultdict(list)
    for row in ownership:
        lid, pid = str(row["league_id"]), str(row["player_id"])
        owner_lookup[(lid, pid)] = row
        by_player[pid].append(row)

    # Exposure matrix: all players owned by the user in >=1 league.
    my_player_ids = sorted({
        str(r["player_id"]) for r in ownership if truthy(r.get("is_my_roster"))
    })
    exposure_rows: list[dict] = []
    for pid in my_player_ids:
        p = players.get(pid, {})
        own_count = start_count = ir_count = taxi_count = opp_count = 0
        row = {
            "player_id": pid,
            "full_name": p.get("full_name") or next((x.get("full_name") for x in by_player[pid] if x.get("full_name")), pid),
            "position": p.get("position") or next((x.get("position") for x in by_player[pid] if x.get("position")), None),
            "nfl_team": p.get("team") or next((x.get("nfl_team") for x in by_player[pid] if x.get("nfl_team")), None),
            "injury_status": p.get("injury_status"),
            "practice_participation": p.get("practice_participation"),
            "depth_chart_position": p.get("depth_chart_position"),
            "depth_chart_order": p.get("depth_chart_order"),
        }
        owned_leagues = []
        started_leagues = []
        opponent_leagues = []
        free_leagues = []
        for lid in league_ids:
            label = labels.get(lid, league_names.get(lid, lid))
            current = owner_lookup.get((lid, pid))
            col = f"league_{lid}"
            if current:
                rid = str(current.get("roster_id"))
                if truthy(current.get("is_my_roster")):
                    state = slot_label(current)
                    row[col] = state
                    own_count += 1
                    owned_leagues.append(label)
                    if state == "START":
                        start_count += 1
                        started_leagues.append(label)
                    elif state == "IR":
                        ir_count += 1
                    elif state == "TAXI":
                        taxi_count += 1
                elif rid in opponent_rosters.get(lid, set()):
                    row[col] = "OPP"
                    opp_count += 1
                    opponent_leagues.append(label)
                else:
                    row[col] = "OWNED"
            else:
                row[col] = "FA"
                free_leagues.append(label)
        row.update({
            "leagues_owned": own_count,
            "exposure_pct": round(100 * own_count / len(league_ids), 1),
            "leagues_started": start_count,
            "starter_exposure_pct": round(100 * start_count / len(league_ids), 1),
            "leagues_ir": ir_count,
            "leagues_taxi": taxi_count,
            "opponent_leagues": opp_count,
            "owned_league_names": " | ".join(owned_leagues),
            "started_league_names": " | ".join(started_leagues),
            "opponent_league_names": " | ".join(opponent_leagues),
            "free_agent_league_names": " | ".join(free_leagues),
        })
        exposure_rows.append(row)

    exposure_rows.sort(key=lambda r: (-int(r["leagues_owned"]), -int(r["leagues_started"]), str(r.get("position") or ""), str(r["full_name"])))

    # Availability matrix: relevant active players plus anyone rostered in these leagues.
    rostered_ids = set(by_player)
    universe = {
        str(pid) for pid, p in players.items()
        if fantasy_relevant(p) and (p.get("active") is True or str(pid) in rostered_ids)
    } | rostered_ids

    availability_rows: list[dict] = []
    for pid in universe:
        p = players.get(pid, {})
        row = {
            "player_id": pid,
            "full_name": p.get("full_name") or next((x.get("full_name") for x in by_player.get(pid, []) if x.get("full_name")), pid),
            "position": p.get("position") or next((x.get("position") for x in by_player.get(pid, []) if x.get("position")), None),
            "nfl_team": p.get("team") or next((x.get("nfl_team") for x in by_player.get(pid, []) if x.get("nfl_team")), None),
            "active": p.get("active"),
            "status": p.get("status"),
            "injury_status": p.get("injury_status"),
            "practice_participation": p.get("practice_participation"),
            "depth_chart_position": p.get("depth_chart_position"),
            "depth_chart_order": p.get("depth_chart_order"),
        }
        fa_count = owned_count = mine_count = opponent_count = starter_count = 0
        for lid in league_ids:
            current = owner_lookup.get((lid, pid))
            col = f"league_{lid}"
            if current is None:
                row[col] = "FA"
                fa_count += 1
            elif truthy(current.get("is_my_roster")):
                row[col] = slot_label(current)
                mine_count += 1
                owned_count += 1
                if row[col] == "START":
                    starter_count += 1
            elif str(current.get("roster_id")) in opponent_rosters.get(lid, set()):
                row[col] = "OPP"
                opponent_count += 1
                owned_count += 1
            else:
                row[col] = "OWNED"
                owned_count += 1
        row.update({
            "free_agent_leagues": fa_count,
            "rostered_leagues": owned_count,
            "my_leagues": mine_count,
            "my_start_leagues": starter_count,
            "opponent_leagues": opponent_count,
            "multi_league_free_agent": fa_count >= 2,
            "rostered_somewhere_free_somewhere": owned_count > 0 and fa_count > 0,
        })
        availability_rows.append(row)

    availability_rows.sort(key=lambda r: (
        -int(r["my_leagues"]),
        -int(r["rostered_leagues"]),
        int(r["free_agent_leagues"]),
        str(r.get("position") or ""),
        str(r["full_name"]),
    ))

    # NFL-team concentration for portfolio-level injury/bye exposure.
    team_counts = defaultdict(lambda: {"owned": 0, "starts": 0, "players": set(), "started_players": set()})
    for r in exposure_rows:
        team = r.get("nfl_team") or "FA/UNK"
        team_counts[team]["owned"] += int(r["leagues_owned"])
        team_counts[team]["starts"] += int(r["leagues_started"])
        team_counts[team]["players"].add(r["full_name"])
        if int(r["leagues_started"]):
            team_counts[team]["started_players"].add(r["full_name"])
    team_rows = []
    for team, vals in team_counts.items():
        team_rows.append({
            "nfl_team": team,
            "roster_slots_across_leagues": vals["owned"],
            "starting_slots_across_leagues": vals["starts"],
            "unique_players_owned": len(vals["players"]),
            "players_owned": " | ".join(sorted(vals["players"])),
            "players_started": " | ".join(sorted(vals["started_players"])),
        })
    team_rows.sort(key=lambda r: (-int(r["starting_slots_across_leagues"]), -int(r["roster_slots_across_leagues"]), r["nfl_team"]))

    # Opponent player exposure this week.
    opp_rows = []
    opp_counter = Counter()
    for row in ownership:
        lid = str(row["league_id"])
        if str(row.get("roster_id")) not in opponent_rosters.get(lid, set()):
            continue
        pid = str(row["player_id"])
        opp_counter[pid] += 1
    for pid, count in opp_counter.most_common():
        p = players.get(pid, {})
        opp_rows.append({
            "player_id": pid,
            "full_name": p.get("full_name") or next((x.get("full_name") for x in by_player[pid] if x.get("full_name")), pid),
            "position": p.get("position"),
            "nfl_team": p.get("team"),
            "opponent_leagues": count,
            "injury_status": p.get("injury_status"),
        })

    write_csv("my_exposure.csv", exposure_rows)
    write_csv("availability_matrix.csv", availability_rows)
    write_csv("nfl_team_exposure.csv", team_rows)
    write_csv("opponent_exposure.csv", opp_rows)

    # Add queryable analysis tables to SQLite without disturbing source tables.
    con = sqlite3.connect(DB)
    try:
        for table, rows in {
            "my_exposure": exposure_rows,
            "availability_matrix": availability_rows,
            "nfl_team_exposure": team_rows,
            "opponent_exposure": opp_rows,
        }.items():
            con.execute(f'DROP TABLE IF EXISTS "{table}"')
            if not rows:
                continue
            fields = list(rows[0].keys())
            cols = ", ".join(f'"{c}" TEXT' for c in fields)
            con.execute(f'CREATE TABLE "{table}" ({cols})')
            qs = ",".join("?" for _ in fields)
            cols_sql = ",".join(f'"{c}"' for c in fields)
            con.executemany(
                f'INSERT INTO "{table}" ({cols_sql}) VALUES ({qs})',
                [[None if row.get(c) is None else str(row.get(c)) for c in fields] for row in rows],
            )
        con.execute("CREATE INDEX IF NOT EXISTS idx_availability_name ON availability_matrix(full_name)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_exposure_owned ON my_exposure(leagues_owned)")
        con.commit()
    finally:
        con.close()

    high_exposure = [r for r in exposure_rows if int(r["leagues_owned"]) >= 2]
    market_signal = [
        r for r in availability_rows
        if str(r["rostered_somewhere_free_somewhere"]) == "True" and int(r["free_agent_leagues"]) >= 1
    ]
    market_signal.sort(key=lambda r: (-int(r["rostered_leagues"]), -int(r["free_agent_leagues"]), str(r["full_name"])))

    exposure_summary = {
        "snapshot_utc": summary.get("snapshot_utc"),
        "current_week": current_week,
        "leagues": len(league_ids),
        "unique_players_owned": len(exposure_rows),
        "players_owned_in_multiple_leagues": len(high_exposure),
        "players_started_in_multiple_leagues": sum(1 for r in exposure_rows if int(r["leagues_started"]) >= 2),
        "players_faced_in_multiple_leagues": sum(1 for r in opp_rows if int(r["opponent_leagues"]) >= 2),
        "top_owned_exposure": [
            {k: r[k] for k in ["player_id", "full_name", "position", "nfl_team", "leagues_owned", "leagues_started", "exposure_pct"]}
            for r in exposure_rows[:20]
        ],
        "top_nfl_team_exposure": team_rows[:12],
        "market_signal_players": [
            {k: r[k] for k in ["player_id", "full_name", "position", "nfl_team", "rostered_leagues", "free_agent_leagues", "my_leagues"]}
            for r in market_signal[:50]
        ],
        "opponent_roster_ids": {k: sorted(v) for k, v in opponent_rosters.items()},
    }
    (ROOT / "data" / "exposure_summary.json").write_text(
        json.dumps(exposure_summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(exposure_summary, indent=2))


if __name__ == "__main__":
    main()

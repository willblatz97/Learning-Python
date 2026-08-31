from __future__ import annotations

import csv
import gzip
import io
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "fantasy_tracker.sqlite"
CONFIG = ROOT / "config.json"

SEASON = 2026
BASELINE_SEASON = 2025
DEPTH_URL = f"https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_{SEASON}.csv.gz"
ROSTER_URL = f"https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_{SEASON}.csv"
STATS_URL = f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{BASELINE_SEASON}.csv"

TEAM_FIX = {"JAC": "JAX", "WSH": "WAS", "LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR"}


def team_fix(v: str | None) -> str:
    x = str(v or "").strip().upper()
    return TEAM_FIX.get(x, x)


def fetch_bytes(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "blatzzy-fantasy-tracker/1.0"})
    with urlopen(req, timeout=90) as r:
        return r.read()


def rows_from_url(url: str, gz: bool = False):
    raw = fetch_bytes(url)
    if gz:
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def read_csv(name: str) -> list[dict]:
    p = OUT / name
    if not p.exists():
        return []
    with p.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(name: str, rows: list[dict]):
    p = OUT / name
    p.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        p.write_text("", encoding="utf-8")
        return
    fields = []
    seen = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                fields.append(k)
    with p.open("w", newline="", encoding="utf-8") as f:
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


def num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def integer(v, default=99) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def first_present(row: dict, names: list[str]) -> float:
    for name in names:
        if name in row and str(row.get(name) or "").strip() not in {"", "NA", "nan"}:
            return num(row.get(name))
    return 0.0


def return_type(row: dict) -> str | None:
    abb = str(row.get("pos_abb") or "").strip().upper()
    name = str(row.get("pos_name") or "").strip().upper()
    slot = str(row.get("pos_slot") or "").strip().upper()
    combined = f"{abb} {name} {slot}"
    # ESPN/nflverse normally uses KR/PR. Keep broader aliases for source changes.
    if abb in {"KR", "KOR"} or "KICK RETURN" in combined or "KICKOFF RETURN" in combined:
        return "KR"
    if abb == "PR" or "PUNT RETURN" in combined:
        return "PR"
    return None


def scoring_profile(row: dict) -> dict:
    return {
        "kr_yd": num(row.get("scoring_kr_yd")),
        "pr_yd": num(row.get("scoring_pr_yd")),
        "return_td": num(row.get("scoring_st_td")),
    }


def confidence(has_history: bool, kr_rank: int | None, pr_rank: int | None) -> str:
    primary = kr_rank == 1 or pr_rank == 1
    if has_history and primary:
        return "HIGH"
    if primary:
        return "MEDIUM"
    if has_history:
        return "MEDIUM"
    return "LOW"


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    league_ids = [str(x) for x in cfg["league_ids"]]
    labels = cfg.get("league_labels", {})
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    leagues = read_csv("leagues.csv")
    availability = read_csv("availability_matrix.csv")
    ownership = read_csv("ownership.csv")
    rankings = read_csv("external_rankings.csv")
    league_by_id = {str(r.get("league_id")): r for r in leagues}
    availability_by_pid = {str(r.get("player_id")): r for r in availability}

    # Current public NFL sources.
    depth_rows = rows_from_url(DEPTH_URL, gz=True)
    roster_rows = rows_from_url(ROSTER_URL)
    stats_rows = rows_from_url(STATS_URL)

    # nflverse current rosters provide the deterministic gsis -> Sleeper bridge.
    gsis_to_sleeper = {}
    espn_to_sleeper = {}
    sleeper_meta = {}
    for r in roster_rows:
        sid = str(r.get("sleeper_id") or "").strip()
        if not sid or sid in {"NA", "None"}:
            continue
        gsis = str(r.get("gsis_id") or "").strip()
        espn = str(r.get("espn_id") or "").strip()
        if gsis and gsis not in {"NA", "None"}:
            gsis_to_sleeper[gsis] = sid
        if espn and espn not in {"NA", "None"}:
            espn_to_sleeper[espn] = sid
        sleeper_meta[sid] = r

    # Keep only the most recent depth-chart snapshot for each NFL team.
    max_dt = {}
    for r in depth_rows:
        team = team_fix(r.get("team"))
        dt = str(r.get("dt") or "")
        if team and (team not in max_dt or dt > max_dt[team]):
            max_dt[team] = dt
    latest = [r for r in depth_rows if str(r.get("dt") or "") == max_dt.get(team_fix(r.get("team")))]

    # Offensive depth context for dual-role returners.
    offense_by_id = {}
    for r in latest:
        if "OFF" not in str(r.get("pos_grp") or "").upper():
            continue
        sid = gsis_to_sleeper.get(str(r.get("gsis_id") or "").strip()) or espn_to_sleeper.get(str(r.get("espn_id") or "").strip())
        if not sid:
            continue
        rank = integer(r.get("pos_rank"))
        old = offense_by_id.get(sid)
        if old is None or rank < integer(old.get("pos_rank")):
            offense_by_id[sid] = r

    # 2025 weekly return baseline. Names are alias-tolerant because nflverse has changed schemas before.
    stat_return_cols = [c for c in (stats_rows[0].keys() if stats_rows else []) if "return" in c.lower()]
    hist = defaultdict(lambda: {"games": 0, "kr_att": 0.0, "kr_yd": 0.0, "kr_td": 0.0, "pr_att": 0.0, "pr_yd": 0.0, "pr_td": 0.0})
    for r in stats_rows:
        if str(r.get("season_type") or "REG").upper() not in {"REG", ""}:
            continue
        gsis = str(r.get("player_id") or r.get("gsis_id") or "").strip()
        if not gsis:
            continue
        h = hist[gsis]
        h["games"] += 1
        h["kr_att"] += first_present(r, ["kick_returns", "kick_return_attempts", "kickoff_returns", "kickoff_return_attempts"])
        h["kr_yd"] += first_present(r, ["kick_return_yards", "kickoff_return_yards"])
        h["kr_td"] += first_present(r, ["kick_return_tds", "kick_return_touchdowns", "kickoff_return_tds"])
        h["pr_att"] += first_present(r, ["punt_returns", "punt_return_attempts"])
        h["pr_yd"] += first_present(r, ["punt_return_yards"])
        h["pr_td"] += first_present(r, ["punt_return_tds", "punt_return_touchdowns"])

    # Current return roles, one row per player, with KR/PR rank if listed at either slot.
    role_map = {}
    unmatched = []
    role_values = set()
    for r in latest:
        rt = return_type(r)
        if not rt:
            continue
        role_values.add(f"{r.get('pos_abb')}|{r.get('pos_name')}")
        gsis = str(r.get("gsis_id") or "").strip()
        espn = str(r.get("espn_id") or "").strip()
        sid = gsis_to_sleeper.get(gsis) or espn_to_sleeper.get(espn)
        if not sid:
            unmatched.append({"team": team_fix(r.get("team")), "player_name": r.get("player_name"), "gsis_id": gsis, "espn_id": espn, "return_type": rt})
            continue
        entry = role_map.setdefault(sid, {"sleeper_id": sid, "gsis_id": gsis, "team": team_fix(r.get("team")), "player_name": r.get("player_name"), "source_dt": r.get("dt"), "kr_rank": None, "pr_rank": None})
        rank = integer(r.get("pos_rank"))
        key = "kr_rank" if rt == "KR" else "pr_rank"
        if entry[key] is None or rank < entry[key]:
            entry[key] = rank

    # User ownership lookup by player and league.
    mine = defaultdict(list)
    starting = defaultdict(list)
    for r in ownership:
        if str(r.get("is_my_roster")).lower() != "true":
            continue
        pid = str(r.get("player_id"))
        lid = str(r.get("league_id"))
        mine[pid].append(labels.get(lid, league_by_id.get(lid, {}).get("name", lid)))
        if str(r.get("starter")).lower() == "true":
            starting[pid].append(labels.get(lid, league_by_id.get(lid, {}).get("name", lid)))

    # Redraft ECR as a generic offensive-value signal for the return report.
    ecr = {}
    for r in rankings:
        if str(r.get("ecr_type")) != "ro":
            continue
        pid = str(r.get("sleeper_id") or "")
        if pid:
            ecr[pid] = num(r.get("ecr"), 9999)

    league_scoring_rows = []
    scoring_by_lid = {}
    for lid in league_ids:
        lr = league_by_id.get(lid, {})
        sp = scoring_profile(lr)
        scoring_by_lid[lid] = sp
        league_scoring_rows.append({
            "league_id": lid,
            "league": labels.get(lid, lr.get("name", lid)),
            "status": lr.get("status"),
            "kr_points_per_yard": sp["kr_yd"],
            "pr_points_per_yard": sp["pr_yd"],
            "individual_return_td_points": sp["return_td"],
            "return_yards_enabled": str(sp["kr_yd"] != 0 or sp["pr_yd"] != 0),
        })

    return_rows = []
    league_boost_rows = []
    for sid, role in role_map.items():
        meta = sleeper_meta.get(sid, {})
        gsis = str(role.get("gsis_id") or meta.get("gsis_id") or "").strip()
        h = hist.get(gsis, {})
        games = int(h.get("games") or 0)
        kr_rank = role.get("kr_rank")
        pr_rank = role.get("pr_rank")
        kr_weight = 1.0 if kr_rank == 1 else 0.35 if kr_rank == 2 else 0.15 if kr_rank else 0.0
        pr_weight = 1.0 if pr_rank == 1 else 0.35 if pr_rank == 2 else 0.15 if pr_rank else 0.0
        has_hist = bool(games and (h.get("kr_att", 0) > 0 or h.get("pr_att", 0) > 0))

        if has_hist:
            kr_y_pg = h.get("kr_yd", 0.0) / games
            pr_y_pg = h.get("pr_yd", 0.0) / games
            kr_td_pg = h.get("kr_td", 0.0) / games
            pr_td_pg = h.get("pr_td", 0.0) / games
            hist_source = "2025 weekly player stats"
        else:
            # Conservative role-based fallback for rookies/new returners; explicitly low/medium confidence.
            kr_y_pg = 22.0 if kr_rank == 1 else 8.0 if kr_rank == 2 else 0.0
            pr_y_pg = 7.0 if pr_rank == 1 else 2.0 if pr_rank == 2 else 0.0
            kr_td_pg = pr_td_pg = 0.0
            hist_source = "role-based fallback"

        proj_kr_y = round(kr_y_pg * kr_weight, 1)
        proj_pr_y = round(pr_y_pg * pr_weight, 1)
        proj_ret_td = round(kr_td_pg * kr_weight + pr_td_pg * pr_weight, 4)
        off = offense_by_id.get(sid, {})
        off_rank = integer(off.get("pos_rank"), 99) if off else None
        dual_role = bool(off and off_rank is not None and off_rank <= 3)
        av = availability_by_pid.get(sid, {})
        free_names = str(av.get("free_agent_league_names") or "")
        conf = confidence(has_hist, kr_rank, pr_rank)

        return_rows.append({
            "snapshot_utc": ts,
            "source_dt": role.get("source_dt"),
            "sleeper_id": sid,
            "gsis_id": gsis,
            "player": meta.get("full_name") or role.get("player_name"),
            "position": meta.get("position"),
            "nfl_team": team_fix(meta.get("team") or role.get("team")),
            "kr_rank": kr_rank,
            "pr_rank": pr_rank,
            "return_roles": "+".join(x for x, rank in [("KR", kr_rank), ("PR", pr_rank)] if rank is not None),
            "offensive_depth_position": off.get("pos_abb") or off.get("pos_name"),
            "offensive_depth_rank": off_rank,
            "dual_role_offense_return": str(dual_role),
            "baseline_games": games,
            "baseline_kr_yards": round(h.get("kr_yd", 0.0), 1),
            "baseline_pr_yards": round(h.get("pr_yd", 0.0), 1),
            "projected_kr_yards_per_game": proj_kr_y,
            "projected_pr_yards_per_game": proj_pr_y,
            "projected_return_td_per_game": proj_ret_td,
            "projection_source": hist_source,
            "confidence": conf,
            "redraft_ecr": None if ecr.get(sid, 9999) >= 9999 else ecr.get(sid),
            "my_leagues": " | ".join(sorted(set(mine.get(sid, [])))),
            "my_starting_leagues": " | ".join(sorted(set(starting.get(sid, [])))),
            "free_agent_leagues": free_names,
        })

        for lid in league_ids:
            sp = scoring_by_lid[lid]
            if sp["kr_yd"] == 0 and sp["pr_yd"] == 0 and sp["return_td"] == 0:
                boost = 0.0
            else:
                boost = proj_kr_y * sp["kr_yd"] + proj_pr_y * sp["pr_yd"] + proj_ret_td * sp["return_td"]
            league_boost_rows.append({
                "league_id": lid,
                "league": labels.get(lid, league_by_id.get(lid, {}).get("name", lid)),
                "sleeper_id": sid,
                "player": meta.get("full_name") or role.get("player_name"),
                "position": meta.get("position"),
                "nfl_team": team_fix(meta.get("team") or role.get("team")),
                "return_roles": "+".join(x for x, rank in [("KR", kr_rank), ("PR", pr_rank)] if rank is not None),
                "dual_role_offense_return": str(dual_role),
                "return_projection_points": round(boost, 2),
                "confidence": conf,
                "roster_state": av.get(f"league_{lid}"),
            })

    # Put primary dual-role returners first, then projected return yards.
    return_rows.sort(key=lambda r: (
        -int(str(r.get("dual_role_offense_return")).lower() == "true"),
        -(1 if r.get("kr_rank") == 1 or r.get("pr_rank") == 1 else 0),
        -(num(r.get("projected_kr_yards_per_game")) + num(r.get("projected_pr_yards_per_game"))),
        num(r.get("redraft_ecr"), 9999),
        str(r.get("player") or ""),
    ))
    league_boost_rows.sort(key=lambda r: (str(r.get("league_id")), -num(r.get("return_projection_points")), str(r.get("player") or "")))

    write_csv("return_roles.csv", return_rows)
    write_csv("league_return_boosts.csv", league_boost_rows)
    write_csv("league_return_scoring.csv", league_scoring_rows)
    write_csv("unmatched_return_roles.csv", unmatched)
    replace_table("return_roles", return_rows)
    replace_table("league_return_boosts", league_boost_rows)
    replace_table("league_return_scoring", league_scoring_rows)

    enabled = [r for r in league_scoring_rows if r["return_yards_enabled"] == "True" or num(r["individual_return_td_points"]) != 0]
    summary = {
        "snapshot_utc": ts,
        "depth_chart_source": DEPTH_URL,
        "roster_id_bridge_source": ROSTER_URL,
        "baseline_stats_source": STATS_URL,
        "depth_chart_teams": len(max_dt),
        "latest_depth_dates": sorted(set(max_dt.values())),
        "return_role_labels_detected": sorted(role_values),
        "returners_mapped_to_sleeper": len(return_rows),
        "returners_unmatched": len(unmatched),
        "stat_return_columns_detected": stat_return_cols,
        "leagues_with_individual_return_scoring": [r["league"] for r in enabled],
        "league_scoring": league_scoring_rows,
    }
    (ROOT / "data" / "returner_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    lines = ["# Returner Intelligence", "", f"Depth chart snapshot dates: {', '.join(summary['latest_depth_dates'])}", ""]
    lines += ["## League return scoring"]
    for r in league_scoring_rows:
        lines.append(f"- {r['league']}: KR {r['kr_points_per_yard']} pts/yd; PR {r['pr_points_per_yard']} pts/yd; return TD {r['individual_return_td_points']} pts")
    lines += ["", "## Top dual-role / primary returners"]
    for r in return_rows[:40]:
        if r.get("kr_rank") != 1 and r.get("pr_rank") != 1:
            continue
        dual = " + offensive role" if r.get("dual_role_offense_return") == "True" else ""
        own = f"; yours: {r['my_leagues']}" if r.get("my_leagues") else ""
        free = f"; FA: {r['free_agent_leagues']}" if r.get("free_agent_leagues") else ""
        lines.append(f"- {r['player']} ({r['position']} {r['nfl_team']}) — {r['return_roles']} {r['kr_rank'] or ''}/{r['pr_rank'] or ''}{dual}; proj return yds {num(r['projected_kr_yards_per_game']) + num(r['projected_pr_yards_per_game']):.1f}/g; {r['confidence']} confidence{own}{free}")
    lines += ["", "## League-specific return boosts"]
    for lid in league_ids:
        lr = league_by_id.get(lid, {})
        label = labels.get(lid, lr.get("name", lid))
        rows = [r for r in league_boost_rows if r["league_id"] == lid and num(r["return_projection_points"]) > 0]
        lines.append(f"### {label}")
        if not rows:
            lines.append("- No individual return-scoring boost in current Sleeper settings.")
        else:
            for r in rows[:10]:
                lines.append(f"- {r['player']} — +{r['return_projection_points']} pts/g return projection ({r['return_roles']}, {r['confidence']}) — {r['roster_state']}")
        lines.append("")
    (ROOT / "data" / "returner_report.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({
        "mapped_returners": len(return_rows),
        "unmatched_returners": len(unmatched),
        "return_role_labels": sorted(role_values),
        "return_stat_columns": stat_return_cols,
        "return_scoring_leagues": [r["league"] for r in enabled],
    }, indent=2))


if __name__ == "__main__":
    main()

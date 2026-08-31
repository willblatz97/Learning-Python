from __future__ import annotations

import csv
import json
import math
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
DB = ROOT / "data" / "fantasy_tracker.sqlite"
CONFIG = ROOT / "config.json"
SUMMARY = ROOT / "data" / "summary.json"
WEEKLY_SUMMARY = ROOT / "data" / "weekly_context_summary.json"


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


def truthy(v) -> bool:
    return str(v).lower() in {"true", "1", "yes"}


def fnum(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def inum(v, default=0):
    x = fnum(v, None)
    return int(x) if x is not None else default


def league_kind(label: str) -> str:
    s = label.lower()
    if "chopped" in s:
        return "chopped"
    if "dynasty" in s:
        return "dynasty"
    if "keeper" in s:
        return "keeper"
    return "redraft"


def eligible(slot: str, pos: str) -> bool:
    if slot == pos:
        return True
    if slot == "FLEX":
        return pos in {"RB", "WR", "TE"}
    if slot in {"REC_FLEX", "WRRB_FLEX"}:
        return pos in {"RB", "WR", "TE"}
    if slot == "SUPER_FLEX":
        return pos in {"QB", "RB", "WR", "TE"}
    return False


def rank_type(kind: str, slots: list[str]) -> str:
    if kind == "dynasty":
        return "dsf" if "SUPER_FLEX" in slots else "do"
    return "ro"


def proxy_from_ecr(ecr, pos: str) -> float:
    e = fnum(ecr, None)
    if e is None:
        return 5.0
    score = 22.5 - 0.047 * min(e, 400)
    if pos == "QB":
        score += 2.0
    elif pos == "TE":
        score -= 1.0
    elif pos in {"K", "DEF", "DL", "LB", "DB"}:
        score -= 6.0
    return max(1.0, round(score, 2))


def injury_adjust(status: str, practice: str) -> tuple[float, str]:
    s = str(status or "").lower()
    p = str(practice or "").lower()
    penalty = 0.0
    notes = []
    if "ir" in s or "pup" in s:
        penalty -= 30; notes.append("IR/PUP")
    elif s in {"out", "inactive"} or "out" == s:
        penalty -= 30; notes.append("OUT")
    elif "doubt" in s:
        penalty -= 10; notes.append("DOUBTFUL")
    elif "question" in s:
        penalty -= 2.5; notes.append("QUESTIONABLE")
    if "did not" in p or p == "dnp":
        penalty -= 2.0; notes.append("DNP")
    elif "limited" in p:
        penalty -= 0.8; notes.append("LIMITED")
    return penalty, "/".join(notes)


def replace_table(name: str, rows: list[dict]):
    con = sqlite3.connect(DB)
    try:
        con.execute(f'DROP TABLE IF EXISTS "{name}"')
        if rows:
            fields = list(rows[0].keys())
            cols_def = ", ".join(f'"{c}" TEXT' for c in fields)
            con.execute(f'CREATE TABLE "{name}" ({cols_def})')
            qs = ",".join("?" for _ in fields)
            cols = ",".join(f'"{c}"' for c in fields)
            con.executemany(
                f'INSERT INTO "{name}" ({cols}) VALUES ({qs})',
                [[None if r.get(c) is None else str(r.get(c)) for c in fields] for r in rows],
            )
        con.commit()
    finally:
        con.close()


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    weekly_summary = json.loads(WEEKLY_SUMMARY.read_text(encoding="utf-8")) if WEEKLY_SUMMARY.exists() else {}
    week = int((summary.get("nfl_state") or {}).get("week") or 1)
    season = int((summary.get("nfl_state") or {}).get("season") or 2026)
    labels = cfg.get("league_labels", {})

    leagues = {str(r["league_id"]): r for r in read_csv("leagues.csv")}
    rosters = read_csv("rosters.csv")
    ownership = read_csv("ownership.csv")
    matchups = read_csv("matchups.csv")
    rankings = read_csv("external_rankings.csv")
    weekly = read_csv("weekly_rankings.csv")
    team_context = {str(r.get("team")): r for r in read_csv("team_week_context.csv")}

    current_weekly = bool(weekly_summary.get("weekly_rankings_current_season"))
    weekly_lookup = {str(r.get("sleeper_id")): r for r in weekly} if current_weekly else {}
    ranking_lookup = {(str(r.get("sleeper_id")), str(r.get("ecr_type"))): r for r in rankings}
    roster_lookup = {(str(r["league_id"]), str(r["roster_id"])): r for r in rosters}
    ownership_by_roster = defaultdict(list)
    player_own_lookup = {}
    for r in ownership:
        key = (str(r["league_id"]), str(r["roster_id"]))
        ownership_by_roster[key].append(r)
        player_own_lookup[(str(r["league_id"]), str(r["roster_id"]), str(r["player_id"]))] = r

    matchup_rows = [m for m in matchups if inum(m.get("week")) == week]
    matchup_lookup = {(str(m["league_id"]), str(m["roster_id"])): m for m in matchup_rows}
    matchup_groups = defaultdict(list)
    for m in matchup_rows:
        mid = str(m.get("matchup_id") or "")
        if mid:
            matchup_groups[(str(m["league_id"]), mid)].append(str(m["roster_id"]))

    player_score_rows = []
    score_lookup = {}
    decision_rows = []
    matchup_summary_rows = []
    report = {"snapshot_utc": summary.get("snapshot_utc"), "season": season, "week": week, "weekly_consensus_current": current_weekly, "leagues": {}}
    lines = [f"# Saturday Lineup & Matchup Board — Week {week}", ""]
    if not current_weekly:
        lines += ["Weekly consensus has not refreshed for the 2026 season yet, so current scores are preseason/season-long lineup proxies. The engine will automatically switch to weekly consensus/projection points when the source updates.", ""]

    active_ids = [str(x) for x in cfg["league_ids"] if leagues.get(str(x), {}).get("status") != "pre_draft"]

    for lid in active_ids:
        lrow = leagues[lid]
        label = labels.get(lid, lrow.get("name", lid))
        kind = league_kind(label)
        try:
            slots_all = json.loads(lrow.get("roster_positions") or "[]")
        except json.JSONDecodeError:
            slots_all = []
        lineup_slots = [s for s in slots_all if s not in {"BN", "IR", "TAXI"}]
        rtype = rank_type(kind, lineup_slots)

        # Score every rostered player in the league once.
        for r in [x for x in ownership if str(x["league_id"]) == lid]:
            pid = str(r.get("player_id")); pos = str(r.get("position") or "")
            wr = weekly_lookup.get(pid, {})
            rr = ranking_lookup.get((pid, rtype), {})
            weekly_pts = fnum(wr.get("projected_points"), None)
            weekly_ecr = fnum(wr.get("ecr"), None)
            season_ecr = fnum(rr.get("ecr"), None)
            if weekly_pts is not None:
                base = weekly_pts; source = "weekly projected points"
            elif current_weekly and weekly_ecr is not None:
                base = proxy_from_ecr(weekly_ecr, pos); source = "weekly ECR proxy"
            else:
                base = proxy_from_ecr(season_ecr, pos); source = "season-long ECR proxy"
            inj_adj, injury_note = injury_adjust(r.get("injury_status"), r.get("practice_participation"))
            depth = inum(r.get("depth_chart_order"), 99)
            depth_adj = 0.5 if depth == 1 else -0.7 if depth == 2 else -1.5 if depth >= 3 and pos not in {"K","DEF"} else 0.0
            ctx = team_context.get(str(r.get("nfl_team") or ""), {})
            total = fnum(ctx.get("total_line"), None)
            env_adj = 0.6 if total is not None and total >= 50 else -0.5 if total is not None and total <= 39 else 0.0
            roof = str(ctx.get("roof") or "")
            wind = fnum(ctx.get("wind"), None)
            temp = fnum(ctx.get("temp"), None)
            weather_adj = 0.0
            weather_note = ""
            if roof in {"outdoors", "open", "retractable"}:
                if wind is not None and wind >= 20 and pos in {"QB","WR","K"}:
                    weather_adj -= 1.2; weather_note = f"wind {wind:g} mph"
                if temp is not None and temp <= 30:
                    weather_adj -= 0.4; weather_note = (weather_note + ", " if weather_note else "") + f"temp {temp:g}F"
            score = round(max(0.0, base + inj_adj + depth_adj + env_adj + weather_adj), 2)
            out = {
                "league_id": lid, "league": label, "roster_id": r.get("roster_id"), "is_my_roster": r.get("is_my_roster"),
                "player_id": pid, "player": r.get("full_name"), "position": pos, "nfl_team": r.get("nfl_team"),
                "opponent": ctx.get("opponent"), "home_away": ctx.get("home_away"), "gameday": ctx.get("gameday"), "gametime": ctx.get("gametime"),
                "total_line": ctx.get("total_line"), "roof": roof, "temp": ctx.get("temp"), "wind": ctx.get("wind"),
                "lineup_score": score, "score_source": source, "weekly_ecr": weekly_ecr, "season_ecr": season_ecr,
                "weekly_grade": wr.get("start_sit_grade"), "injury_status": r.get("injury_status"), "practice_participation": r.get("practice_participation"),
                "injury_note": injury_note, "depth_chart_order": r.get("depth_chart_order"), "weather_note": weather_note,
            }
            player_score_rows.append(out)
            score_lookup[(lid, str(r.get("roster_id")), pid)] = out

        my_r = next((r for r in rosters if str(r["league_id"]) == lid and truthy(r.get("is_my_roster"))), None)
        if not my_r:
            continue
        my_rid = str(my_r["roster_id"])
        mm = matchup_lookup.get((lid, my_rid), {})
        try:
            starters = json.loads(mm.get("starters") or "[]")
        except json.JSONDecodeError:
            starters = []
        roster_players = [str(r.get("player_id")) for r in ownership_by_roster[(lid, my_rid)] if not truthy(r.get("reserve")) and not truthy(r.get("taxi"))]
        bench = [pid for pid in roster_players if pid not in starters]

        suggestions = []
        monitors = []
        for idx, slot in enumerate(lineup_slots):
            if idx >= len(starters):
                continue
            starter_pid = str(starters[idx])
            starter = score_lookup.get((lid, my_rid, starter_pid))
            if not starter:
                continue
            candidates = []
            for pid in bench:
                cand = score_lookup.get((lid, my_rid, pid))
                if cand and eligible(slot, str(cand.get("position") or "")):
                    candidates.append(cand)
            candidates.sort(key=lambda x: -float(x["lineup_score"]))
            best = candidates[0] if candidates else None
            delta = round(float(best["lineup_score"]) - float(starter["lineup_score"]), 2) if best else None
            risk = str(starter.get("injury_note") or "")
            if "OUT" in risk or "IR/PUP" in risk:
                action = "MUST CHANGE" if best else "NO REPLACEMENT"
            elif best and delta is not None and delta >= 2.0:
                action = "CHANGE"
            elif "DOUBTFUL" in risk or "QUESTIONABLE" in risk or "DNP" in risk:
                action = "MONITOR"
            elif best and delta is not None and delta >= 0.8:
                action = "LEAN CHANGE"
            else:
                action = "HOLD"
            row = {
                "league_id": lid, "league": label, "slot": slot, "action": action,
                "starter_id": starter_pid, "starter": starter.get("player"), "starter_pos": starter.get("position"), "starter_score": starter.get("lineup_score"),
                "starter_injury": starter.get("injury_note"), "starter_nfl_opponent": starter.get("opponent"),
                "best_bench_id": best.get("player_id") if best else None, "best_bench": best.get("player") if best else None,
                "best_bench_pos": best.get("position") if best else None, "best_bench_score": best.get("lineup_score") if best else None,
                "score_delta": delta, "score_source": starter.get("score_source"),
            }
            decision_rows.append(row)
            if action in {"MUST CHANGE","CHANGE","LEAN CHANGE","NO REPLACEMENT"}:
                suggestions.append(row)
            elif action == "MONITOR":
                monitors.append(row)

        # Current lineup score for any roster in league, based on Sleeper starters.
        roster_scores = []
        for rr in [r for r in rosters if str(r["league_id"]) == lid]:
            rid = str(rr["roster_id"])
            m = matchup_lookup.get((lid, rid), {})
            try: sids = json.loads(m.get("starters") or "[]")
            except json.JSONDecodeError: sids = []
            total_score = sum(float(score_lookup.get((lid, rid, str(pid)), {}).get("lineup_score") or 0) for pid in sids)
            roster_scores.append((rid, round(total_score, 2), rr))
        my_score = next((s for rid, s, rr in roster_scores if rid == my_rid), 0.0)

        opponent_name = None; opp_score = None; projected_margin = None
        if kind != "chopped":
            mid = str(mm.get("matchup_id") or "")
            opp_ids = [rid for rid in matchup_groups.get((lid, mid), []) if rid != my_rid]
            if opp_ids:
                opp_rid = opp_ids[0]
                opp = roster_lookup.get((lid, opp_rid), {})
                opponent_name = opp.get("team_name") or opp.get("owner_display_name")
                opp_score = next((s for rid, s, rr in roster_scores if rid == opp_rid), 0.0)
                projected_margin = round(my_score - opp_score, 2)
        else:
            ordered = sorted(roster_scores, key=lambda x: x[1], reverse=True)
            my_rank = next((i + 1 for i, (rid, s, rr) in enumerate(ordered) if rid == my_rid), None)
            others = [s for rid, s, rr in roster_scores if rid != my_rid]
            cutoff = min(others) if others else 0.0
            projected_margin = round(my_score - cutoff, 2)
            opponent_name = f"Projected rank {my_rank}/{len(ordered)}"
            opp_score = round(cutoff, 2)

        matchup_summary_rows.append({
            "league_id": lid, "league": label, "league_type": kind, "team": my_r.get("team_name"),
            "opponent_or_chop_rank": opponent_name, "my_lineup_score": my_score, "opponent_or_chop_line": opp_score,
            "projected_margin": projected_margin, "recommended_changes": len(suggestions), "injury_monitors": len(monitors),
            "score_mode": "weekly consensus" if current_weekly else "season-long proxy",
        })
        report["leagues"][lid] = {
            "league": label, "type": kind, "team": my_r.get("team_name"), "opponent_or_chop_rank": opponent_name,
            "my_lineup_score": my_score, "opponent_or_chop_line": opp_score, "projected_margin": projected_margin,
            "recommended_changes": suggestions, "monitors": monitors,
        }

        lines += [f"## {label}", f"Team: {my_r.get('team_name')}"]
        if kind == "chopped":
            lines.append(f"{opponent_name} — lineup proxy {my_score:.2f}; projected chop line {opp_score:.2f}; safety margin {projected_margin:+.2f}")
        else:
            lines.append(f"Opponent: {opponent_name or 'TBD'} — lineup proxy {my_score:.2f} vs {opp_score if opp_score is not None else 0:.2f}; margin {projected_margin if projected_margin is not None else 0:+.2f}")
        lines += ["", "### Lineup actions"]
        if not suggestions:
            lines.append("No lineup changes clear the current action threshold.")
        for d in suggestions:
            repl = f" -> {d['best_bench']} ({d['best_bench_score']})" if d.get("best_bench") else " -> no legal bench replacement"
            lines.append(f"- {d['action']} {d['slot']}: {d['starter']} ({d['starter_score']}){repl}; delta {d.get('score_delta')}; injury {d.get('starter_injury') or 'clear'}")
        lines += ["", "### Injury / practice watch"]
        if not monitors:
            lines.append("No monitor-level starter flags.")
        for d in monitors:
            lines.append(f"- {d['starter']} — {d.get('starter_injury') or 'monitor'}; best legal bench option: {d.get('best_bench') or 'none'}")
        lines.append("")

    write_csv("player_week_scores.csv", player_score_rows)
    write_csv("lineup_decisions.csv", decision_rows)
    write_csv("matchup_summary.csv", matchup_summary_rows)
    replace_table("player_week_scores", player_score_rows)
    replace_table("lineup_decisions", decision_rows)
    replace_table("matchup_summary", matchup_summary_rows)
    (ROOT / "data" / "saturday_summary.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (ROOT / "data" / "saturday_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"player_scores": len(player_score_rows), "lineup_decisions": len(decision_rows), "matchups": len(matchup_summary_rows), "weekly_consensus_current": current_weekly}, indent=2))


if __name__ == "__main__":
    main()

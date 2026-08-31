from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
DB = ROOT / "data" / "fantasy_tracker.sqlite"
CONFIG = ROOT / "config.json"
SUMMARY = ROOT / "data" / "saturday_summary.json"


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
                seen.add(k); fields.append(k)
    with (OUT / name).open("w", newline="", encoding="utf-8") as f:
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
            con.executemany(
                f'INSERT INTO "{name}" ({cols}) VALUES ({qs})',
                [[None if r.get(c) is None else str(r.get(c)) for c in fields] for r in rows],
            )
        con.commit()
    finally:
        con.close()


def truthy(v) -> bool:
    return str(v).lower() in {"true", "1", "yes"}


def num(v, default=0.0):
    try: return float(v)
    except (TypeError, ValueError): return default


def inum(v, default=0):
    try: return int(float(v))
    except (TypeError, ValueError): return default


def eligible(slot: str, pos: str) -> bool:
    if slot == pos: return True
    if slot in {"FLEX", "REC_FLEX", "WRRB_FLEX"}: return pos in {"RB", "WR", "TE"}
    if slot == "SUPER_FLEX": return pos in {"QB", "RB", "WR", "TE"}
    return False


def league_kind(label: str) -> str:
    s = str(label or "").lower()
    if "chopped" in s: return "chopped"
    if "dynasty" in s: return "dynasty"
    if "keeper" in s: return "keeper"
    return "redraft"


def conf_factor(v: str) -> float:
    s = str(v or "").upper()
    return 1.0 if s == "HIGH" else 0.8 if s == "MEDIUM" else 0.6


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    old = json.loads(SUMMARY.read_text(encoding="utf-8"))
    labels = cfg.get("league_labels", {})
    week = old.get("week")

    leagues = {str(r.get("league_id")): r for r in read_csv("leagues.csv")}
    rosters = read_csv("rosters.csv")
    ownership = read_csv("ownership.csv")
    matchups = [r for r in read_csv("matchups.csv") if str(r.get("week")) == str(week)]
    scores = read_csv("player_week_scores.csv")
    boosts = read_csv("league_return_boosts.csv")

    boost_lookup = {(str(r.get("league_id")), str(r.get("sleeper_id"))): r for r in boosts}
    adjusted = 0
    for r in scores:
        key = (str(r.get("league_id")), str(r.get("player_id")))
        b = boost_lookup.get(key)
        base = num(r.get("lineup_score"))
        r["base_lineup_score_before_return"] = round(base, 2)
        if not b or num(b.get("return_projection_points")) <= 0:
            r["return_projection_points"] = 0.0
            r["return_points_applied"] = 0.0
            continue
        raw = num(b.get("return_projection_points"))
        applied = round(raw * conf_factor(b.get("confidence")), 2)
        r["return_projection_points"] = raw
        r["return_points_applied"] = applied
        r["return_roles"] = b.get("return_roles")
        r["return_confidence"] = b.get("confidence")
        r["dual_role_offense_return"] = b.get("dual_role_offense_return")
        r["offensive_depth_rank"] = b.get("offensive_depth_rank")
        r["lineup_score"] = round(base + applied, 2)
        r["score_source"] = str(r.get("score_source") or "") + " + league return scoring"
        adjusted += 1

    score_lookup = {(str(r.get("league_id")), str(r.get("roster_id")), str(r.get("player_id"))): r for r in scores}
    roster_lookup = {(str(r.get("league_id")), str(r.get("roster_id"))): r for r in rosters}
    ownership_by_roster = defaultdict(list)
    for r in ownership:
        ownership_by_roster[(str(r.get("league_id")), str(r.get("roster_id")))].append(r)
    matchup_lookup = {(str(r.get("league_id")), str(r.get("roster_id"))): r for r in matchups}
    matchup_groups = defaultdict(list)
    for m in matchups:
        mid = str(m.get("matchup_id") or "")
        if mid:
            matchup_groups[(str(m.get("league_id")), mid)].append(str(m.get("roster_id")))

    decision_rows = []
    matchup_rows = []
    report = {
        "snapshot_utc": old.get("snapshot_utc"),
        "season": old.get("season"),
        "week": week,
        "weekly_consensus_current": old.get("weekly_consensus_current"),
        "return_scoring_integrated": True,
        "leagues": {},
    }

    active = [str(x) for x in cfg["league_ids"] if leagues.get(str(x), {}).get("status") != "pre_draft"]
    for lid in active:
        lrow = leagues[lid]
        label = labels.get(lid, lrow.get("name", lid))
        kind = league_kind(label)
        try: all_slots = json.loads(lrow.get("roster_positions") or "[]")
        except json.JSONDecodeError: all_slots = []
        slots = [s for s in all_slots if s not in {"BN", "IR", "TAXI"}]
        mine = next((r for r in rosters if str(r.get("league_id")) == lid and truthy(r.get("is_my_roster"))), None)
        if not mine: continue
        myrid = str(mine.get("roster_id"))
        mm = matchup_lookup.get((lid, myrid), {})
        try: starters = [str(x) for x in json.loads(mm.get("starters") or "[]")]
        except json.JSONDecodeError: starters = []
        roster_players = [str(r.get("player_id")) for r in ownership_by_roster[(lid, myrid)] if not truthy(r.get("reserve")) and not truthy(r.get("taxi"))]
        bench = [pid for pid in roster_players if pid not in starters]

        suggestions, monitors = [], []
        for idx, slot in enumerate(slots):
            if idx >= len(starters): break
            spid = starters[idx]
            starter = score_lookup.get((lid, myrid, spid))
            if not starter: continue
            candidates = []
            for pid in bench:
                c = score_lookup.get((lid, myrid, pid))
                if c and eligible(slot, str(c.get("position") or "")):
                    candidates.append(c)
            candidates.sort(key=lambda x: -num(x.get("lineup_score")))
            best = candidates[0] if candidates else None
            delta = round(num(best.get("lineup_score")) - num(starter.get("lineup_score")), 2) if best else None
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
            d = {
                "league_id": lid, "league": label, "slot": slot, "action": action,
                "starter_id": spid, "starter": starter.get("player"), "starter_pos": starter.get("position"), "starter_score": starter.get("lineup_score"),
                "starter_return_points": starter.get("return_points_applied", 0), "starter_return_roles": starter.get("return_roles"),
                "starter_injury": starter.get("injury_note"), "starter_nfl_opponent": starter.get("opponent"),
                "best_bench_id": best.get("player_id") if best else None, "best_bench": best.get("player") if best else None,
                "best_bench_pos": best.get("position") if best else None, "best_bench_score": best.get("lineup_score") if best else None,
                "best_bench_return_points": best.get("return_points_applied", 0) if best else None,
                "best_bench_return_roles": best.get("return_roles") if best else None,
                "score_delta": delta, "score_source": starter.get("score_source"),
            }
            decision_rows.append(d)
            if action in {"MUST CHANGE", "CHANGE", "LEAN CHANGE", "NO REPLACEMENT"}: suggestions.append(d)
            elif action == "MONITOR": monitors.append(d)

        roster_scores = []
        for rr in [x for x in rosters if str(x.get("league_id")) == lid]:
            rid = str(rr.get("roster_id"))
            m = matchup_lookup.get((lid, rid), {})
            try: sids = [str(x) for x in json.loads(m.get("starters") or "[]")]
            except json.JSONDecodeError: sids = []
            total = round(sum(num(score_lookup.get((lid, rid, pid), {}).get("lineup_score")) for pid in sids), 2)
            roster_scores.append((rid, total, rr))
        my_score = next((s for rid, s, rr in roster_scores if rid == myrid), 0.0)

        opponent_name = None; line = None; margin = None
        if kind != "chopped":
            mid = str(mm.get("matchup_id") or "")
            opp_ids = [rid for rid in matchup_groups.get((lid, mid), []) if rid != myrid]
            if opp_ids:
                oppid = opp_ids[0]
                opp = roster_lookup.get((lid, oppid), {})
                opponent_name = opp.get("team_name") or opp.get("owner_display_name")
                line = next((s for rid, s, rr in roster_scores if rid == oppid), 0.0)
                margin = round(my_score - line, 2)
        else:
            ordered = sorted(roster_scores, key=lambda x: x[1], reverse=True)
            rank = next((i + 1 for i, (rid, s, rr) in enumerate(ordered) if rid == myrid), None)
            others = [s for rid, s, rr in roster_scores if rid != myrid]
            line = min(others) if others else 0.0
            margin = round(my_score - line, 2)
            opponent_name = f"Projected rank {rank}/{len(ordered)}"

        matchup_rows.append({
            "league_id": lid, "league": label, "league_type": kind, "team": mine.get("team_name"),
            "opponent_or_chop_rank": opponent_name, "my_lineup_score": my_score, "opponent_or_chop_line": line,
            "projected_margin": margin, "recommended_changes": len(suggestions), "injury_monitors": len(monitors),
            "score_mode": "weekly consensus + return scoring" if old.get("weekly_consensus_current") else "season-long proxy + return scoring",
        })
        report["leagues"][lid] = {
            "league": label, "type": kind, "team": mine.get("team_name"), "opponent_or_chop_rank": opponent_name,
            "my_lineup_score": my_score, "opponent_or_chop_line": line, "projected_margin": margin,
            "recommended_changes": suggestions, "monitors": monitors,
        }

    write_csv("player_week_scores.csv", scores)
    write_csv("lineup_decisions.csv", decision_rows)
    write_csv("matchup_summary.csv", matchup_rows)
    replace_table("player_week_scores", scores)
    replace_table("lineup_decisions", decision_rows)
    replace_table("matchup_summary", matchup_rows)
    SUMMARY.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"return_adjusted_player_scores": adjusted, "lineup_decisions_recomputed": len(decision_rows), "matchups_recomputed": len(matchup_rows)}, indent=2))


if __name__ == "__main__":
    main()

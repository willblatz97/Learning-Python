from __future__ import annotations

import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "fantasy_tracker.sqlite"
SUMMARY = ROOT / "data" / "summary.json"
CONFIG = ROOT / "config.json"


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


def truthy(v) -> bool:
    return str(v).lower() in {"true", "1", "yes"}


def safe_int(v, default=0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def replace_sqlite_table(table: str, rows: list[dict]):
    con = sqlite3.connect(DB)
    try:
        con.execute(f'DROP TABLE IF EXISTS "{table}"')
        if not rows:
            con.commit()
            return
        fields = list(rows[0].keys())
        cols = ", ".join(f'"{c}" TEXT' for c in fields)
        con.execute(f'CREATE TABLE "{table}" ({cols})')
        qs = ",".join("?" for _ in fields)
        col_sql = ",".join(f'"{c}"' for c in fields)
        con.executemany(
            f'INSERT INTO "{table}" ({col_sql}) VALUES ({qs})',
            [[None if row.get(c) is None else str(row.get(c)) for c in fields] for row in rows],
        )
        con.commit()
    finally:
        con.close()


def load_players() -> dict:
    p = RAW / "players_nfl.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def league_kind(label: str) -> str:
    x = label.lower()
    if "chopped" in x:
        return "chopped"
    if "dynasty" in x:
        return "dynasty"
    if "keeper" in x:
        return "keeper"
    return "redraft"


def player_age(p: dict) -> int | None:
    age = p.get("age")
    if age not in (None, ""):
        try:
            return int(age)
        except (TypeError, ValueError):
            pass
    return None


def injury_penalty(status: str | None) -> float:
    s = str(status or "").lower()
    if not s:
        return 3.0
    if "question" in s:
        return -4.0
    if "doubt" in s:
        return -10.0
    if s in {"out", "inactive"}:
        return -14.0
    if "ir" in s or "pup" in s:
        return -24.0
    return -2.0


def depth_score(order) -> float:
    n = safe_int(order, 99)
    return {1: 20.0, 2: 10.0, 3: 4.0}.get(n, -2.0 if n >= 5 else 0.0)


def starter_need(roster_positions: list[str]) -> dict[str, float]:
    need = Counter()
    for slot in roster_positions:
        if slot in {"QB", "RB", "WR", "TE", "K", "DEF", "DL", "LB", "DB"}:
            need[slot] += 1.0
        elif slot == "SUPER_FLEX":
            need["QB"] += 0.65
            need["RB"] += 0.10
            need["WR"] += 0.15
            need["TE"] += 0.10
        elif slot == "FLEX":
            need["RB"] += 0.30
            need["WR"] += 0.50
            need["TE"] += 0.20
        elif slot in {"REC_FLEX", "WRRB_FLEX"}:
            need["RB"] += 0.35
            need["WR"] += 0.55
            need["TE"] += 0.10
    return dict(need)


def faab_band(score: float, kind: str) -> tuple[int, int]:
    if score >= 82:
        low, high = 22, 35
    elif score >= 72:
        low, high = 14, 22
    elif score >= 62:
        low, high = 8, 14
    elif score >= 52:
        low, high = 4, 8
    elif score >= 43:
        low, high = 1, 4
    else:
        low, high = 0, 1
    if kind == "chopped":
        low = min(50, math.ceil(low * 1.35))
        high = min(60, math.ceil(high * 1.45))
    elif kind == "dynasty":
        high = min(40, math.ceil(high * 1.10))
    return low, high


def priority(score: float) -> str:
    if score >= 82:
        return "MUST ADD"
    if score >= 70:
        return "HIGH"
    if score >= 58:
        return "MEDIUM"
    if score >= 46:
        return "SPECULATIVE"
    return "WATCH"


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    labels = cfg.get("league_labels", {})
    league_ids = [str(x) for x in cfg["league_ids"]]
    players = load_players()

    leagues = read_csv("leagues.csv")
    availability = read_csv("availability_matrix.csv")
    ownership = read_csv("ownership.csv")
    rosters = read_csv("rosters.csv")
    transactions = read_csv("transactions.csv")

    league_by_id = {str(r["league_id"]): r for r in leagues}
    active_leagues = [lid for lid in league_ids if league_by_id.get(lid, {}).get("status") != "pre_draft"]

    my_roster = {
        str(r["league_id"]): r
        for r in rosters if truthy(r.get("is_my_roster"))
    }
    my_ownership = defaultdict(list)
    for r in ownership:
        if truthy(r.get("is_my_roster")):
            my_ownership[str(r["league_id"])].append(r)

    # Across-league add/drop momentum. This is deliberately a signal, not a projection.
    add_counts, drop_counts = Counter(), Counter()
    for tx in transactions:
        if str(tx.get("status")) not in {"complete", "pending", ""}:
            continue
        try:
            adds = json.loads(tx.get("adds") or "{}")
        except json.JSONDecodeError:
            adds = {}
        try:
            drops = json.loads(tx.get("drops") or "{}")
        except json.JSONDecodeError:
            drops = {}
        for pid in adds or {}:
            add_counts[str(pid)] += 1
        for pid in drops or {}:
            drop_counts[str(pid)] += 1

    # Position inventory and starter need by league.
    position_counts = {}
    position_needs = {}
    for lid in active_leagues:
        counts = Counter()
        for r in my_ownership.get(lid, []):
            pos = str(r.get("position") or "")
            if pos:
                counts[pos] += 1
        position_counts[lid] = counts
        try:
            slots = json.loads(league_by_id[lid].get("roster_positions") or "[]")
        except json.JSONDecodeError:
            slots = []
        position_needs[lid] = starter_need(slots)

    waiver_rows: list[dict] = []
    cut_rows: list[dict] = []
    per_league_summary = {}

    for lid in active_leagues:
        lrow = league_by_id[lid]
        label = labels.get(lid, lrow.get("name", lid))
        kind = league_kind(label)
        total_rosters = safe_int(lrow.get("total_rosters"), 12)
        budget_total = safe_int(lrow.get("setting_waiver_budget"), 100)
        roster_settings = {}
        try:
            roster_settings = json.loads(my_roster.get(lid, {}).get("settings") or "{}")
        except json.JSONDecodeError:
            pass
        budget_used = safe_int(roster_settings.get("waiver_budget_used"), 0)
        budget_remaining = max(0, budget_total - budget_used)

        # Candidate add rankings.
        league_adds = []
        for a in availability:
            if a.get(f"league_{lid}") != "FA":
                continue
            pid = str(a.get("player_id") or "")
            pos = str(a.get("position") or "")
            if pos not in {"QB", "RB", "WR", "TE", "K", "DEF", "DL", "LB", "DB"}:
                continue
            p = players.get(pid, {})
            active = str(a.get("active")).lower() == "true"
            if not active and pos not in {"DEF"}:
                continue

            # Market score excludes the league where the player is being evaluated.
            other_rostered = 0
            other_possible = 0
            for other in active_leagues:
                if other == lid:
                    continue
                state = a.get(f"league_{other}")
                if state in {"START", "BENCH", "IR", "TAXI", "OWNED", "OPP"}:
                    other_rostered += 1
                if state != "PREDRAFT":
                    other_possible += 1
            market_rate = (other_rostered / other_possible) if other_possible else 0.0

            score = 20.0 + market_rate * 36.0
            score += depth_score(a.get("depth_chart_order"))
            score += injury_penalty(a.get("injury_status"))
            score += min(14.0, add_counts[pid] * 4.0)
            score -= min(10.0, drop_counts[pid] * 3.0)

            need = position_needs[lid].get(pos, 0.0)
            have = position_counts[lid].get(pos, 0)
            if need and have <= math.ceil(need) + 1:
                score += 8.0
            elif need and have <= math.ceil(need) + 3:
                score += 3.0

            age = player_age(p)
            years_exp = safe_int(p.get("years_exp"), 99)
            if kind == "dynasty":
                if years_exp <= 2:
                    score += 7.0
                elif age and age >= 30 and pos not in {"QB", "TE"}:
                    score -= 4.0
            elif kind == "chopped":
                if safe_int(a.get("depth_chart_order"), 99) == 1:
                    score += 7.0
                if str(a.get("injury_status") or ""):
                    score -= 4.0
                if pos in {"K", "DEF"}:
                    score -= 3.0

            # Deep leagues give stronger weight to proven market scarcity.
            if total_rosters >= 16:
                score += market_rate * 8.0
            score = round(max(0.0, min(100.0, score)), 1)
            low_pct, high_pct = faab_band(score, kind)
            low_dollars = math.ceil(budget_remaining * low_pct / 100)
            high_dollars = math.ceil(budget_remaining * high_pct / 100)

            reasons = []
            if market_rate >= 0.66:
                reasons.append("rostered in most comparable leagues")
            if safe_int(a.get("depth_chart_order"), 99) == 1:
                reasons.append("depth-chart starter")
            elif safe_int(a.get("depth_chart_order"), 99) == 2:
                reasons.append("primary backup")
            if add_counts[pid]:
                reasons.append(f"{add_counts[pid]} recent cross-league add(s)")
            if need and have <= math.ceil(need) + 1:
                reasons.append(f"thin {pos} depth on your roster")
            if kind == "dynasty" and years_exp <= 2:
                reasons.append("young dynasty stash value")
            if a.get("injury_status"):
                reasons.append(f"injury: {a.get('injury_status')}")

            out = {
                "league_id": lid,
                "league": label,
                "league_type": kind,
                "player_id": pid,
                "player": a.get("full_name"),
                "position": pos,
                "nfl_team": a.get("nfl_team"),
                "waiver_score": score,
                "priority": priority(score),
                "depth_chart_order": a.get("depth_chart_order"),
                "injury_status": a.get("injury_status"),
                "rostered_other_active_leagues": other_rostered,
                "other_active_leagues_checked": other_possible,
                "recent_adds": add_counts[pid],
                "recent_drops": drop_counts[pid],
                "faab_remaining": budget_remaining,
                "faab_low_pct": low_pct,
                "faab_high_pct": high_pct,
                "faab_low": low_dollars,
                "faab_high": high_dollars,
                "reasons": " | ".join(reasons) if reasons else "market/depth-chart watch",
            }
            league_adds.append(out)

        league_adds.sort(key=lambda r: (-float(r["waiver_score"]), str(r["position"]), str(r["player"])))
        for rank, row in enumerate(league_adds[:30], start=1):
            row["league_rank"] = rank
            waiver_rows.append(row)

        # Cut rankings: high score = easier/safer to cut. Starters are protected heavily.
        league_cuts = []
        for r in my_ownership.get(lid, []):
            pid = str(r.get("player_id") or "")
            pos = str(r.get("position") or "")
            p = players.get(pid, {})
            state = "START" if truthy(r.get("starter")) else "IR" if truthy(r.get("reserve")) else "TAXI" if truthy(r.get("taxi")) else "BENCH"
            score = 35.0
            if state == "START":
                score -= 60.0
            elif state == "TAXI":
                score -= 20.0
            elif state == "IR":
                score -= 5.0

            order = safe_int(r.get("depth_chart_order"), 99)
            if order == 1:
                score -= 25.0
            elif order == 2:
                score -= 10.0
            elif order >= 4:
                score += 10.0

            # Protect players with external market demand.
            external_rostered = 0
            for other in active_leagues:
                if other == lid:
                    continue
                arow = next((x for x in availability if str(x.get("player_id")) == pid), None)
                if arow and arow.get(f"league_{other}") in {"START", "BENCH", "IR", "TAXI", "OWNED", "OPP"}:
                    external_rostered += 1
            score -= external_rostered * 10.0
            score -= min(8.0, add_counts[pid] * 3.0)
            score += min(8.0, drop_counts[pid] * 3.0)

            status = str(r.get("status") or "").lower()
            inj = str(r.get("injury_status") or "").lower()
            if status in {"inactive", "retired"}:
                score += 25.0
            if "ir" in inj:
                score += 6.0

            have = position_counts[lid].get(pos, 0)
            need = position_needs[lid].get(pos, 0.0)
            surplus = have - math.ceil(need)
            if surplus >= 4:
                score += 8.0
            elif surplus >= 2:
                score += 4.0

            years_exp = safe_int(p.get("years_exp"), 99)
            age = player_age(p)
            if kind == "dynasty":
                if years_exp <= 2:
                    score -= 12.0
                elif age and age >= 30 and pos not in {"QB", "TE"}:
                    score += 6.0
            elif kind in {"redraft", "chopped"} and pos in {"K", "DEF"} and state != "START":
                score += 10.0

            score = round(max(0.0, min(100.0, score)), 1)
            reasons = []
            if state == "START":
                reasons.append("starter protected")
            if order >= 4:
                reasons.append("buried on depth chart")
            if external_rostered == 0:
                reasons.append("little cross-league market demand")
            elif external_rostered >= 2:
                reasons.append("strong cross-league market demand")
            if surplus >= 4:
                reasons.append(f"surplus {pos} depth")
            if kind == "dynasty" and years_exp <= 2:
                reasons.append("young dynasty asset protected")
            if status in {"inactive", "retired"}:
                reasons.append(status)

            league_cuts.append({
                "league_id": lid,
                "league": label,
                "league_type": kind,
                "player_id": pid,
                "player": r.get("full_name"),
                "position": pos,
                "nfl_team": r.get("nfl_team"),
                "roster_state": state,
                "cut_score": score,
                "cut_tier": "SAFE CUT" if score >= 70 else "CUTTABLE" if score >= 55 else "ONLY IF NEEDED" if score >= 40 else "HOLD",
                "depth_chart_order": r.get("depth_chart_order"),
                "injury_status": r.get("injury_status"),
                "external_rostered_leagues": external_rostered,
                "recent_adds": add_counts[pid],
                "recent_drops": drop_counts[pid],
                "reasons": " | ".join(reasons) if reasons else "roster-value hold",
            })

        league_cuts.sort(key=lambda r: (-float(r["cut_score"]), str(r["player"])))
        for rank, row in enumerate(league_cuts[:12], start=1):
            row["league_rank"] = rank
            cut_rows.append(row)

        per_league_summary[lid] = {
            "league": label,
            "type": kind,
            "status": lrow.get("status"),
            "faab_total": budget_total,
            "faab_used": budget_used,
            "faab_remaining": budget_remaining,
            "top_adds": [
                {k: x.get(k) for k in ["player", "position", "nfl_team", "waiver_score", "priority", "faab_low", "faab_high", "reasons"]}
                for x in league_adds[:8]
            ],
            "top_cuts": [
                {k: x.get(k) for k in ["player", "position", "nfl_team", "cut_score", "cut_tier", "reasons"]}
                for x in league_cuts[:6]
            ],
        }

    write_csv("waiver_candidates.csv", waiver_rows)
    write_csv("cut_candidates.csv", cut_rows)
    replace_sqlite_table("waiver_candidates", waiver_rows)
    replace_sqlite_table("cut_candidates", cut_rows)

    waiver_summary = {
        "snapshot_utc": summary.get("snapshot_utc"),
        "week": (summary.get("nfl_state") or {}).get("week"),
        "active_waiver_leagues": len(active_leagues),
        "skipped_predraft_leagues": [lid for lid in league_ids if lid not in active_leagues],
        "method": "V1 heuristic: cross-league market + depth chart + injuries + transaction momentum + roster need + format",
        "leagues": per_league_summary,
    }
    (ROOT / "data" / "waiver_summary.json").write_text(json.dumps(waiver_summary, indent=2, sort_keys=True), encoding="utf-8")

    # Human-readable artifact for Tuesday automation / quick review.
    lines = [f"# Waiver Board — Week {waiver_summary['week']}", ""]
    for lid in active_leagues:
        info = per_league_summary[lid]
        lines.append(f"## {info['league']}")
        lines.append(f"FAAB remaining: {info['faab_remaining']} / {info['faab_total']}")
        lines.append("")
        lines.append("### Adds")
        for i, x in enumerate(info["top_adds"][:6], 1):
            lines.append(f"{i}. {x['player']} ({x['position']} {x['nfl_team']}) — {x['priority']} {x['waiver_score']} — FAAB {x['faab_low']}-{x['faab_high']} — {x['reasons']}")
        lines.append("")
        lines.append("### Cuts")
        for i, x in enumerate(info["top_cuts"][:5], 1):
            lines.append(f"{i}. {x['player']} ({x['position']} {x['nfl_team']}) — {x['cut_tier']} {x['cut_score']} — {x['reasons']}")
        lines.append("")
    (ROOT / "data" / "waiver_report.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({
        "active_waiver_leagues": len(active_leagues),
        "waiver_candidate_rows": len(waiver_rows),
        "cut_candidate_rows": len(cut_rows),
        "top_add_by_league": {
            lid: (per_league_summary[lid]["top_adds"][0]["player"] if per_league_summary[lid]["top_adds"] else None)
            for lid in active_leagues
        },
    }, indent=2))


if __name__ == "__main__":
    main()

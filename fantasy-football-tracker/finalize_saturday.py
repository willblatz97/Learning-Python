from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
CONFIG = ROOT / "config.json"
SUMMARY = ROOT / "data" / "saturday_summary.json"


def read_csv(name: str) -> list[dict]:
    p = OUT / name
    if not p.exists():
        return []
    with p.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def truthy(v) -> bool:
    return str(v).lower() in {"true", "1", "yes"}


def fnum(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    labels = cfg.get("league_labels", {})
    week = summary.get("week")

    leagues = {str(r["league_id"]): r for r in read_csv("leagues.csv")}
    rosters = read_csv("rosters.csv")
    matchups = [r for r in read_csv("matchups.csv") if str(r.get("week")) == str(week)]
    scores = read_csv("player_week_scores.csv")
    decisions = read_csv("lineup_decisions.csv")

    roster_by_key = {(str(r["league_id"]), str(r["roster_id"])): r for r in rosters}
    my_roster = {str(r["league_id"]): r for r in rosters if truthy(r.get("is_my_roster"))}
    matchup_by_key = {(str(r["league_id"]), str(r["roster_id"])): r for r in matchups}
    grouped_matchups = defaultdict(list)
    for r in matchups:
        mid = str(r.get("matchup_id") or "")
        if mid:
            grouped_matchups[(str(r["league_id"]), mid)].append(str(r["roster_id"]))

    score_by_roster = defaultdict(list)
    for r in scores:
        score_by_roster[(str(r["league_id"]), str(r["roster_id"]))].append(r)

    lines = [f"# Saturday Lineup & Matchup Board — Week {week}", ""]
    if not summary.get("weekly_consensus_current"):
        lines += ["Weekly consensus has not refreshed for the 2026 season yet, so current scores are preseason/season-long lineup proxies. The engine will automatically switch to weekly consensus/projection points when the source updates.", ""]

    for lid in [str(x) for x in cfg["league_ids"]]:
        if leagues.get(lid, {}).get("status") == "pre_draft" or lid not in summary.get("leagues", {}):
            continue
        info = summary["leagues"][lid]
        label = labels.get(lid, info.get("league", lid))
        kind = info.get("type")
        lines += [f"## {label}", f"Team: {info.get('team')}"]
        if kind == "chopped":
            lines.append(f"{info.get('opponent_or_chop_rank')} — lineup proxy {fnum(info.get('my_lineup_score')):.2f}; projected chop line {fnum(info.get('opponent_or_chop_line')):.2f}; safety margin {fnum(info.get('projected_margin')):+.2f}")
        else:
            lines.append(f"Opponent: {info.get('opponent_or_chop_rank') or 'TBD'} — lineup proxy {fnum(info.get('my_lineup_score')):.2f} vs {fnum(info.get('opponent_or_chop_line')):.2f}; margin {fnum(info.get('projected_margin')):+.2f}")

        changes = [d for d in decisions if str(d.get("league_id")) == lid and d.get("action") in {"MUST CHANGE","CHANGE","LEAN CHANGE","NO REPLACEMENT"}]
        monitors = [d for d in decisions if str(d.get("league_id")) == lid and d.get("action") == "MONITOR"]
        lines += ["", "### Lineup actions"]
        if not changes:
            lines.append("No lineup changes clear the current action threshold.")
        for d in changes:
            repl = f" -> {d.get('best_bench')} ({d.get('best_bench_score')})" if d.get("best_bench") else " -> no legal bench replacement"
            lines.append(f"- {d.get('action')} {d.get('slot')}: {d.get('starter')} ({d.get('starter_score')}){repl}; delta {d.get('score_delta')}; injury {d.get('starter_injury') or 'clear'}")

        lines += ["", "### Your injury / practice watch"]
        if not monitors:
            lines.append("No monitor-level starter flags.")
        for d in monitors:
            lines.append(f"- {d.get('starter')} — {d.get('starter_injury') or 'monitor'}; best legal bench option: {d.get('best_bench') or 'none'}")

        if kind != "chopped":
            mine = my_roster.get(lid)
            opp_players = []
            if mine:
                mm = matchup_by_key.get((lid, str(mine.get("roster_id"))), {})
                mid = str(mm.get("matchup_id") or "")
                opp_ids = [rid for rid in grouped_matchups.get((lid, mid), []) if rid != str(mine.get("roster_id"))]
                if opp_ids:
                    opp_rid = opp_ids[0]
                    try:
                        starters = set(json.loads(matchup_by_key.get((lid, opp_rid), {}).get("starters") or "[]"))
                    except json.JSONDecodeError:
                        starters = set()
                    opp_players = [p for p in score_by_roster[(lid, opp_rid)] if str(p.get("player_id")) in {str(x) for x in starters}]
            opp_players.sort(key=lambda p: -fnum(p.get("lineup_score")))
            lines += ["", "### Opponent threats"]
            if not opp_players:
                lines.append("Opponent starters not resolved yet.")
            else:
                for p in opp_players[:3]:
                    lines.append(f"- {p.get('player')} ({p.get('position')} {p.get('nfl_team')}) — {fnum(p.get('lineup_score')):.2f} vs {p.get('opponent') or 'TBD'}")
            injury_threats = [p for p in opp_players if p.get("injury_note")]
            lines += ["", "### Opponent injury leverage"]
            if not injury_threats:
                lines.append("No opponent starter injury/practice flags currently detected.")
            for p in injury_threats:
                lines.append(f"- {p.get('player')} — {p.get('injury_note')}; current lineup score {fnum(p.get('lineup_score')):.2f}")
        else:
            all_other = []
            mine = my_roster.get(lid)
            myrid = str(mine.get("roster_id")) if mine else ""
            for (ll, rid), plist in score_by_roster.items():
                if ll != lid or rid == myrid:
                    continue
                all_other.extend(plist)
            injured = [p for p in all_other if p.get("injury_note") and fnum(p.get("lineup_score")) > 0]
            injured.sort(key=lambda p: -fnum(p.get("lineup_score")))
            lines += ["", "### Chop-field injury leverage"]
            if not injured:
                lines.append("No meaningful injury flags detected across the current field.")
            for p in injured[:5]:
                lines.append(f"- {p.get('player')} ({p.get('position')} {p.get('nfl_team')}) — {p.get('injury_note')}; could lower another roster's floor")
        lines.append("")

    (ROOT / "data" / "saturday_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"week": week, "report_sections": len(summary.get('leagues', {})), "opponent_context": True}, indent=2))


if __name__ == "__main__":
    main()

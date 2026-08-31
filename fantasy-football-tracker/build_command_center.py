from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
DATA = ROOT / "data"
CONFIG = ROOT / "config.json"


def load_json(name: str, default):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

def read_csv(name: str) -> list[dict]:
    p = OUT / name
    if not p.exists(): return []
    with p.open("r", newline="", encoding="utf-8") as f: return list(csv.DictReader(f))
def write_csv(name: str, rows: list[dict]):
    if not rows: return
    fields, seen = [], set()
    for r in rows:
        for k in r:
            if k not in seen: seen.add(k); fields.append(k)
    with (OUT / name).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
def num(v, default=0.0):
    try: return float(v)
    except (TypeError, ValueError): return default

def risk_label(kind: str, margin: float | None) -> str:
    if margin is None: return "NO LIVE MATCHUP"
    if kind == "chopped":
        if margin < 0: return "CHOP RISK"
        if margin < 4: return "DANGER"
        if margin < 9: return "WATCH"
        return "COMFORTABLE"
    if margin <= -6: return "TRAILING"
    if margin < -2: return "LEAN OPPONENT"
    if margin <= 2: return "TOSS-UP"
    if margin < 6: return "LEAN YOU"
    return "FAVORED"

def first_action(sat: dict) -> tuple[str, str]:
    changes = sat.get("recommended_changes") or []
    if changes:
        x = changes[0]
        return str(x.get("action") or "CHANGE"), f"{x.get('slot')}: {x.get('starter')} -> {x.get('best_bench')} ({num(x.get('score_delta')):+.2f})"
    monitors = sat.get("monitors") or []
    if monitors:
        x = monitors[0]; bench = f"; backup {x.get('best_bench')}" if x.get("best_bench") else ""
        return "MONITOR", f"{x.get('starter')} {x.get('starter_injury') or 'status'}{bench}"
    return "HOLD", "No lineup action above threshold"

def main():
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    leagues = {str(r.get("league_id")): r for r in read_csv("leagues.csv")}
    sat = load_json("saturday_summary.json", {"leagues": {}}); wai = load_json("waiver_summary.json", {"leagues": {}})
    usage = load_json("usage_summary.json", {}); ol = load_json("ol_health_summary.json", {}); weather = load_json("weather_summary.json", {})
    projected = load_json("projected_finish.json", {"leagues": {}})
    labels = cfg.get("league_labels", {}); weekly_current = bool(sat.get("weekly_consensus_current")); projection_mode = "WEEKLY CONSENSUS" if weekly_current else "PRESEASON / SEASON-LONG PROXY"

    rows = []
    for lid in [str(x) for x in cfg.get("league_ids", [])]:
        l = leagues.get(lid, {}); status = str(l.get("status") or "unknown"); s = (sat.get("leagues") or {}).get(lid, {}); w = (wai.get("leagues") or {}).get(lid, {})
        po = ((projected.get("leagues") or {}).get(lid, {}) or {}).get("my_outlook") or {}
        label = s.get("league") or w.get("league") or labels.get(lid) or l.get("name") or lid
        if status == "pre_draft":
            rows.append({"league_id":lid,"league":label,"league_status":status,"team":"","format":"keeper/redraft","projection_mode":projection_mode,"risk":"PREDRAFT","my_projection":"","opponent_or_cutoff":"","margin":"","top_action":"DRAFT PENDING","action_detail":"League remains pre-draft; activate roster/matchup intelligence automatically after draft.","top_waiver":"","waiver_priority":"","faab":"","injury_monitors":0,"projected_seed":"","expected_wins":"","playoff_odds":"","finish_range":"","schedule_label":"","schedule_difficulty_percentile":"","roster_power_rank":""})
            continue
        margin = num(s.get("projected_margin"), None); kind = str(s.get("type") or w.get("type") or ""); action, detail = first_action(s); adds = w.get("top_adds") or []; add = adds[0] if adds else {}; faab = f"{add.get('faab_low','')}-{add.get('faab_high','')}" if add else ""
        rows.append({"league_id":lid,"league":label,"league_status":status,"team":s.get("team"),"format":kind,"projection_mode":projection_mode,"risk":risk_label(kind,margin),"my_projection":s.get("my_lineup_score"),"opponent_or_cutoff":s.get("opponent_or_chop_line"),"opponent_or_cutoff_name":s.get("opponent_or_chop_rank"),"margin":margin,"top_action":action,"action_detail":detail,"top_waiver":add.get("player"),"waiver_position":add.get("position"),"waiver_priority":add.get("priority"),"faab":faab,"waiver_reason":add.get("reasons"),"injury_monitors":len(s.get("monitors") or []),"projected_seed":po.get("most_likely_seed"),"expected_wins":po.get("expected_wins"),"playoff_odds":po.get("playoff_odds"),"finish_range":f"#{po.get('finish_range_low')}-#{po.get('finish_range_high')}" if po else "","schedule_label":po.get("schedule_label"),"schedule_difficulty_percentile":po.get("schedule_difficulty_percentile"),"roster_power_rank":po.get("league_power_rank")})

    write_csv("command_center.csv", rows)
    overall = {"snapshot_utc":ts,"season":sat.get("season"),"week":sat.get("week"),"projection_mode":projection_mode,"usage_mode":"CURRENT SEASON ACTIVE" if usage.get("current_season_data") else f"{usage.get('usage_season','prior')} CONTEXT ONLY","ol_mapping_rate":ol.get("mapping_rate"),"ol_degraded_teams":ol.get("degraded_teams") or [],"ol_major_concern_teams":ol.get("major_concern_teams") or [],"weather_high_games":weather.get("high_weather_games") or [],"weather_forecasted_games":weather.get("forecasted_games"),"leagues":rows}
    (DATA / "command_center.json").write_text(json.dumps(overall, indent=2, sort_keys=True), encoding="utf-8")

    lines = [f"# Fantasy Command Center — Week {sat.get('week')}", "", f"Projection mode: **{projection_mode}**", f"Usage: **{overall['usage_mode']}**", ""]
    for r in rows:
        lines += [f"## {r['league']}"]
        if r["risk"] == "PREDRAFT":
            lines += ["- Status: PREDRAFT", f"- {r['action_detail']}", ""]; continue
        margin_text = f"{num(r.get('margin')):+.2f}"
        lines += [f"- **{r['risk']}** — {r.get('team') or 'Your team'} — projection {r.get('my_projection')} vs {r.get('opponent_or_cutoff')} ({r.get('opponent_or_cutoff_name')}); margin {margin_text}",f"- **Action:** {r['top_action']} — {r['action_detail']}"]
        if r.get("projected_seed") and r.get("format") != "chopped": lines.append(f"- **Projected finish:** seed #{r['projected_seed']} · {r.get('expected_wins')} expected wins · {r.get('playoff_odds')}% playoff odds · range {r.get('finish_range')} · roster power #{r.get('roster_power_rank')} · schedule {r.get('schedule_label')} ({r.get('schedule_difficulty_percentile')}th percentile difficulty)")
        if r.get("top_waiver"): lines.append(f"- **Waiver:** {r['top_waiver']} ({r.get('waiver_position')}) — {r.get('waiver_priority')} — FAAB {r.get('faab')} — {r.get('waiver_reason')}")
        if int(r.get("injury_monitors") or 0): lines.append(f"- **Injury decisions:** {r['injury_monitors']} starter monitor(s)")
        lines.append("")
    if overall["ol_degraded_teams"] or overall["ol_major_concern_teams"]: lines += ["## NFL Environment Alerts", f"- OL degraded: {', '.join(overall['ol_degraded_teams'] + overall['ol_major_concern_teams'])}"]
    if overall["weather_high_games"]: lines.append(f"- High-impact weather: {', '.join(overall['weather_high_games'])}")
    elif weather.get("games"): lines.append("- High-impact weather: none currently")
    (DATA / "command_center.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"leagues":len(rows),"projection_mode":projection_mode,"output":"data/command_center.md"},indent=2))

if __name__ == "__main__": main()

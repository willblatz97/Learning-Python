from __future__ import annotations

import csv
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
DB = ROOT / "data" / "fantasy_tracker.sqlite"
CONFIG = ROOT / "config.json"
SUMMARY = ROOT / "data" / "summary.json"


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
    if "chopped" in s: return "chopped"
    if "dynasty" in s: return "dynasty"
    if "keeper" in s: return "keeper"
    return "redraft"


def eligible_positions(slots: list[str]) -> set[str]:
    out = {s for s in slots if s in {"QB","RB","WR","TE","K","DEF","DL","LB","DB"}}
    if any(s in slots for s in {"FLEX","REC_FLEX","WRRB_FLEX"}):
        out.update({"RB","WR","TE"})
    if "SUPER_FLEX" in slots:
        out.update({"QB","RB","WR","TE"})
    return out


def quality_points(ecr: float | None) -> float:
    if ecr is None: return 0.0
    if ecr <= 25: return 36.0
    if ecr <= 50: return 31.0
    if ecr <= 100: return 25.0
    if ecr <= 150: return 19.0
    if ecr <= 200: return 13.0
    if ecr <= 250: return 8.0
    if ecr <= 300: return 4.0
    return 0.0


def faab_band(score: float, kind: str) -> tuple[int,int]:
    if score >= 85: lo, hi = 20, 30
    elif score >= 75: lo, hi = 12, 20
    elif score >= 65: lo, hi = 7, 12
    elif score >= 55: lo, hi = 3, 7
    elif score >= 45: lo, hi = 1, 3
    else: lo, hi = 0, 1
    if kind == "chopped":
        lo = min(50, math.ceil(lo * 1.4)); hi = min(60, math.ceil(hi * 1.5))
    return lo, hi


def priority(score: float) -> str:
    if score >= 85: return "MUST ADD"
    if score >= 74: return "HIGH"
    if score >= 62: return "MEDIUM"
    if score >= 50: return "SPECULATIVE"
    return "WATCH"


def replace_table(name: str, rows: list[dict]):
    con = sqlite3.connect(DB)
    try:
        con.execute(f'DROP TABLE IF EXISTS "{name}"')
        if rows:
            fields = list(rows[0].keys())
            con.execute(f'CREATE TABLE "{name}" ({", ".join(f"\"{c}\" TEXT" for c in fields)})')
            q = ",".join("?" for _ in fields)
            cols = ",".join(f'"{c}"' for c in fields)
            con.executemany(f'INSERT INTO "{name}" ({cols}) VALUES ({q})', [[str(r.get(c)) if r.get(c) is not None else None for c in fields] for r in rows])
        con.commit()
    finally:
        con.close()


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    labels = cfg.get("league_labels", {})
    leagues = {str(r["league_id"]): r for r in read_csv("leagues.csv")}
    rankings = read_csv("external_rankings.csv")
    adds = read_csv("waiver_candidates.csv")
    cuts = read_csv("cut_candidates.csv")

    rank_lookup = {(str(r.get("sleeper_id")), str(r.get("ecr_type"))): r for r in rankings}
    slots_by_league = {}
    rank_type_by_league = {}
    for lid, lrow in leagues.items():
        try: slots = json.loads(lrow.get("roster_positions") or "[]")
        except json.JSONDecodeError: slots = []
        slots_by_league[lid] = slots
        kind = league_kind(labels.get(lid, lrow.get("name", lid)))
        rank_type_by_league[lid] = "dsf" if kind == "dynasty" and "SUPER_FLEX" in slots else "do" if kind == "dynasty" else "ro"

    refined_adds = []
    for r in adds:
        lid = str(r["league_id"]); pos = str(r.get("position") or "")
        if pos not in eligible_positions(slots_by_league.get(lid, [])):
            continue
        kind = league_kind(r.get("league") or "")
        rt = rank_type_by_league.get(lid, "ro")
        rr = rank_lookup.get((str(r.get("player_id")), rt), {})
        ecr = fnum(rr.get("ecr"), None)
        owned_avg = fnum(rr.get("player_owned_avg"), 0.0) or 0.0
        old = fnum(r.get("waiver_score"), 0.0) or 0.0
        score = old * 0.45 + quality_points(ecr)
        if owned_avg >= 60: score += 6
        elif owned_avg >= 30: score += 4
        elif owned_avg >= 10: score += 2
        if ecr is None: score -= 5
        score = round(max(0.0, min(100.0, score)), 1)
        lo, hi = faab_band(score, kind)
        remain = inum(r.get("faab_remaining"), 0)
        r.update({
            "heuristic_score": old,
            "waiver_score": score,
            "priority": priority(score),
            "consensus_ecr": ecr,
            "consensus_rank_type": rt,
            "consensus_owned_avg": owned_avg,
            "consensus_scrape_date": rr.get("scrape_date"),
            "faab_low_pct": lo,
            "faab_high_pct": hi,
            "faab_low": math.ceil(remain * lo / 100),
            "faab_high": math.ceil(remain * hi / 100),
            "quality_source": "FantasyPros consensus via DynastyProcess" if rr else "heuristic only",
        })
        if ecr is not None:
            r["reasons"] = f"consensus ECR {ecr:g} | " + str(r.get("reasons") or "")
        refined_adds.append(r)

    refined_adds.sort(key=lambda r: (str(r["league_id"]), -float(r["waiver_score"]), str(r.get("position") or ""), str(r.get("player") or "")))
    grouped = defaultdict(list)
    for r in refined_adds: grouped[str(r["league_id"])].append(r)
    final_adds = []
    for lid, rows in grouped.items():
        for i, r in enumerate(rows[:30], 1): r["league_rank"] = i; final_adds.append(r)

    refined_cuts = []
    for r in cuts:
        lid = str(r["league_id"]); kind = league_kind(r.get("league") or "")
        rt = rank_type_by_league.get(lid, "ro")
        rr = rank_lookup.get((str(r.get("player_id")), rt), {})
        ecr = fnum(rr.get("ecr"), None)
        score = (fnum(r.get("cut_score"), 0.0) or 0.0) * 0.65
        if ecr is not None:
            if ecr <= 100: score -= 35
            elif ecr <= 200: score -= 22
            elif ecr <= 300: score -= 10
        state = str(r.get("roster_state") or "")
        if state == "START": score = min(score, 5)
        if state in {"IR","TAXI"}: score = min(score, 35)
        if kind == "dynasty" and ecr is not None and ecr <= 300: score = min(score, 45)
        score = round(max(0.0, min(100.0, score)), 1)
        tier = "SAFE CUT" if score >= 80 else "CUTTABLE" if score >= 65 else "ONLY IF NEEDED" if score >= 50 else "HOLD"
        r.update({
            "cut_score": score,
            "cut_tier": tier,
            "consensus_ecr": ecr,
            "consensus_rank_type": rt,
            "consensus_owned_avg": fnum(rr.get("player_owned_avg"), None),
            "consensus_scrape_date": rr.get("scrape_date"),
            "quality_source": "FantasyPros consensus via DynastyProcess" if rr else "heuristic only",
        })
        if ecr is not None:
            r["reasons"] = f"consensus ECR {ecr:g} protected | " + str(r.get("reasons") or "")
        refined_cuts.append(r)

    refined_cuts.sort(key=lambda r: (str(r["league_id"]), -float(r["cut_score"]), str(r.get("player") or "")))
    gcut = defaultdict(list)
    for r in refined_cuts: gcut[str(r["league_id"])].append(r)
    final_cuts = []
    for lid, rows in gcut.items():
        for i, r in enumerate(rows[:12], 1): r["league_rank"] = i; final_cuts.append(r)

    write_csv("waiver_candidates.csv", final_adds); write_csv("cut_candidates.csv", final_cuts)
    replace_table("waiver_candidates", final_adds); replace_table("cut_candidates", final_cuts)

    active_ids = [lid for lid in cfg["league_ids"] if leagues.get(str(lid), {}).get("status") != "pre_draft"]
    result = {
        "snapshot_utc": summary.get("snapshot_utc"),
        "week": (summary.get("nfl_state") or {}).get("week"),
        "method": "V1.1: Sleeper availability/rosters + current consensus ECR + depth chart/injury + transaction momentum + roster need + format",
        "ranking_source": "DynastyProcess ffverse FantasyPros consensus mirror",
        "ranking_scrape_dates": sorted({r.get("scrape_date") for r in rankings if r.get("scrape_date")}),
        "skipped_predraft_leagues": [str(lid) for lid in cfg["league_ids"] if str(lid) not in [str(x) for x in active_ids]],
        "leagues": {},
    }
    lines = [f"# Waiver Board — Week {result['week']}", "", "Consensus-enhanced V1.1 board. FAAB is a range, not a precise bid.", ""]
    for lid in [str(x) for x in active_ids]:
        label = labels.get(lid, leagues.get(lid, {}).get("name", lid)); kind = league_kind(label)
        la = [r for r in final_adds if str(r["league_id"]) == lid]
        lc = [r for r in final_cuts if str(r["league_id"]) == lid]
        remain = inum(la[0].get("faab_remaining"), 0) if la else 0
        total = inum(leagues.get(lid, {}).get("setting_waiver_budget"), 100)
        result["leagues"][lid] = {
            "league": label, "type": kind, "faab_remaining": remain,
            "top_adds": [{k:r.get(k) for k in ["player","position","nfl_team","waiver_score","priority","consensus_ecr","faab_low","faab_high","reasons"]} for r in la[:8]],
            "top_cuts": [{k:r.get(k) for k in ["player","position","nfl_team","cut_score","cut_tier","consensus_ecr","reasons"]} for r in lc[:6]],
        }
        lines += [f"## {label}", f"FAAB remaining: {remain} / {total}", "", "### Adds"]
        for i,r in enumerate(la[:6],1): lines.append(f"{i}. {r['player']} ({r['position']} {r['nfl_team']}) — {r['priority']} {r['waiver_score']} — ECR {r.get('consensus_ecr') or 'n/a'} — FAAB {r['faab_low']}-{r['faab_high']} — {r['reasons']}")
        lines += ["", "### Cuts"]
        for i,r in enumerate(lc[:5],1): lines.append(f"{i}. {r['player']} ({r['position']} {r['nfl_team']}) — {r['cut_tier']} {r['cut_score']} — ECR {r.get('consensus_ecr') or 'n/a'} — {r['reasons']}")
        lines.append("")

    (ROOT / "data" / "waiver_summary.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    (ROOT / "data" / "waiver_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"refined_add_rows":len(final_adds),"refined_cut_rows":len(final_cuts),"ranking_dates":result["ranking_scrape_dates"]}, indent=2))


if __name__ == "__main__":
    main()

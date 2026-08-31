from __future__ import annotations

import csv
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"


def read_csv(name: str) -> list[dict]:
    p = OUT / name
    return list(csv.DictReader(p.open(encoding="utf-8"))) if p.exists() else []


def write_csv(name: str, rows: list[dict]):
    if not rows:
        return
    fields=[]; seen=set()
    for row in rows:
        for k in row:
            if k not in seen:
                seen.add(k); fields.append(k)
    with (OUT/name).open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def num(v,d=0.0):
    try:return float(v)
    except (TypeError,ValueError):return d


def integer(v,d=99):
    try:return int(float(v))
    except (TypeError,ValueError):return d


def quality(e):
    if e is None:return 0.0
    if e<=25:return 36.0
    if e<=50:return 31.0
    if e<=100:return 25.0
    if e<=150:return 19.0
    if e<=200:return 13.0
    if e<=250:return 8.0
    if e<=300:return 4.0
    return 0.0


def depth(order):
    n=integer(order)
    return {1:20.0,2:10.0,3:4.0}.get(n,-2.0 if n>=5 else 0.0)


def injury(status):
    s=str(status or "").lower()
    if not s:return 3.0
    if "question" in s:return -4.0
    if "doubt" in s:return -10.0
    if s in {"out","inactive"}:return -14.0
    if "ir" in s or "pup" in s:return -24.0
    return -2.0


def main():
    candidates=read_csv("waiver_candidates.csv")
    boosts=read_csv("league_return_boosts.csv")
    availability={str(r.get("player_id")):r for r in read_csv("availability_matrix.csv")}
    rankings=read_csv("external_rankings.csv")
    leagues={str(r.get("league_id")):r for r in read_csv("leagues.csv")}
    existing={(str(r.get("league_id")),str(r.get("player_id"))) for r in candidates}
    redraft={str(r.get("sleeper_id")):r for r in rankings if str(r.get("ecr_type"))=="ro"}
    injected=0

    for b in boosts:
        lid=str(b.get("league_id") or ""); pid=str(b.get("sleeper_id") or "")
        if not lid or not pid or (lid,pid) in existing:continue
        if leagues.get(lid,{}).get("status")=="pre_draft":continue
        if str(b.get("roster_state"))!="FA" or num(b.get("return_projection_points"))<=0:continue
        a=availability.get(pid,{})
        pos=str(a.get("position") or b.get("position") or "")
        if pos not in {"QB","RB","WR","TE","K","DEF"}:continue
        rr=redraft.get(pid,{})
        try:e=float(rr.get("ecr"))
        except (TypeError,ValueError):e=None
        owned=num(rr.get("player_owned_avg"))
        market=num(a.get("rostered_leagues"))
        score=20.0+min(18.0,market*5.0)+depth(a.get("depth_chart_order"))+injury(a.get("injury_status"))+quality(e)
        if owned>=60:score+=6
        elif owned>=30:score+=4
        elif owned>=10:score+=2
        score=round(max(0,min(100,score)),1)
        total=int(num(leagues.get(lid,{}).get("setting_waiver_budget"),100))
        row={
            "league_id":lid,"league":b.get("league"),"league_type":"chopped" if "chopped" in str(b.get("league") or "").lower() else "redraft",
            "player_id":pid,"player":b.get("player"),"position":pos,"nfl_team":b.get("nfl_team"),
            "waiver_score":score,"priority":"WATCH","depth_chart_order":a.get("depth_chart_order"),"injury_status":a.get("injury_status"),
            "rostered_other_active_leagues":a.get("rostered_leagues"),"other_active_leagues_checked":"","recent_adds":0,"recent_drops":0,
            "faab_remaining":total,"faab_low_pct":0,"faab_high_pct":1,"faab_low":0,"faab_high":math.ceil(total*.01),
            "reasons":"return-scoring league candidate injected before final ranking",
            "heuristic_score":score,"consensus_ecr":e,"consensus_rank_type":"ro","consensus_owned_avg":owned,
            "consensus_scrape_date":rr.get("scrape_date"),"quality_source":"FantasyPros consensus via DynastyProcess" if rr else "heuristic only",
        }
        candidates.append(row);existing.add((lid,pid));injected+=1

    write_csv("waiver_candidates.csv",candidates)
    print(json.dumps({"waiver_candidates_after_injection":len(candidates),"return_candidates_injected":injected},indent=2))

if __name__=="__main__":main()

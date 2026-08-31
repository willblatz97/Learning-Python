from __future__ import annotations

import csv
import json
import math
import random
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
DATA = ROOT / "data"
DB = DATA / "fantasy_tracker.sqlite"
CFG = ROOT / "config.json"


def read_csv(name: str) -> list[dict]:
    p = OUT / name
    if not p.exists(): return []
    with p.open("r", newline="", encoding="utf-8") as f: return list(csv.DictReader(f))

def num(v, d=0.0):
    try: return float(v)
    except (TypeError, ValueError): return d

def integer(v, d=0):
    try: return int(float(v))
    except (TypeError, ValueError): return d

def truth(v): return str(v).lower() in {"true","1","yes"}

def kind(label: str) -> str:
    s=label.lower()
    if "chopped" in s:return "chopped"
    if "dynasty" in s:return "dynasty"
    if "keeper" in s:return "keeper"
    return "redraft"

def eligible(slot: str, pos: str) -> bool:
    if slot == pos:return True
    if slot in {"FLEX","REC_FLEX","WRRB_FLEX"}:return pos in {"RB","WR","TE"}
    if slot == "SUPER_FLEX":return pos in {"QB","RB","WR","TE"}
    return False

def parse_settings(r: dict) -> dict:
    try:return json.loads(r.get("settings") or "{}")
    except:return {}

def record(r: dict) -> tuple[float,float,float]:
    s=parse_settings(r); w=num(s.get("wins")); l=num(s.get("losses")); t=num(s.get("ties")); return w,l,t

def optimize_lineup(players: list[dict], slots: list[str]) -> tuple[float,list[dict],float]:
    # Fill constrained slots first, then flexes. Good enough for power ranking and deterministic.
    order=[]
    for s in slots:
        priority=2 if s=="SUPER_FLEX" else 1 if s in {"FLEX","REC_FLEX","WRRB_FLEX"} else 0
        order.append((priority,s))
    order.sort(key=lambda x:x[0])
    remaining=list(players); chosen=[]; total=0.0
    for _,slot in order:
        c=[p for p in remaining if eligible(slot,str(p.get("position") or ""))]
        if not c: continue
        best=max(c,key=lambda p:num(p.get("lineup_score")))
        chosen.append(best); total+=num(best.get("lineup_score")); remaining.remove(best)
    bench=sorted([num(p.get("lineup_score")) for p in remaining if str(p.get("position")) in {"QB","RB","WR","TE"}],reverse=True)
    depth=sum(bench[:4]) * 0.10
    return round(total+depth,2),chosen,round(depth,2)

def pct_rank(values: list[float], x: float) -> float:
    if len(values)<=1:return 50.0
    less=sum(v<x for v in values); equal=sum(v==x for v in values)
    return round(100*(less+0.5*equal)/len(values),1)

def logistic(z: float) -> float:
    z=max(-6,min(6,z)); return 1/(1+math.exp(-z))

def replace_table(name: str, rows: list[dict]):
    con=sqlite3.connect(DB)
    try:
        con.execute(f'DROP TABLE IF EXISTS "{name}"')
        if rows:
            fs=list(rows[0]); defs=', '.join(f'"{c}" TEXT' for c in fs); cols=','.join(f'"{c}"' for c in fs); qs=','.join('?' for _ in fs)
            con.execute(f'CREATE TABLE "{name}" ({defs})')
            con.executemany(f'INSERT INTO "{name}" ({cols}) VALUES ({qs})',[[None if r.get(c) is None else str(r.get(c)) for c in fs] for r in rows])
        con.commit()
    finally: con.close()

def write_csv(name: str, rows: list[dict]):
    if not rows:return
    fs=[]; seen=set()
    for r in rows:
        for k in r:
            if k not in seen:seen.add(k);fs.append(k)
    with (OUT/name).open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fs,extrasaction='ignore');w.writeheader();w.writerows(rows)

def main():
    cfg=json.loads(CFG.read_text(encoding='utf-8')); labels=cfg.get('league_labels',{})
    summary=json.loads((DATA/'summary.json').read_text(encoding='utf-8')); week=integer((summary.get('nfl_state') or {}).get('week'),1)
    leagues={str(r['league_id']):r for r in read_csv('leagues.csv')}; rosters=read_csv('rosters.csv'); scores=read_csv('player_week_scores.csv'); matchups=read_csv('matchups.csv')
    by_roster=defaultdict(list)
    for p in scores:by_roster[(str(p.get('league_id')),str(p.get('roster_id')))].append(p)
    result={'snapshot_utc':summary.get('snapshot_utc'),'week':week,'method':'league-relative roster power + current health + bench depth + actual fantasy H2H schedule Monte Carlo','simulations':5000,'leagues':{}}
    rows=[]; md=[f'# Projected Finish — Week {week}','', 'Projected finish is probabilistic. Roster power uses the latest lineup scoring, current injury status and bench depth; schedule difficulty uses actual Sleeper H2H opponents when available.','']

    for lid,l in leagues.items():
        label=labels.get(lid,l.get('name',lid)); k=kind(label)
        if k=='chopped':continue
        lr=[r for r in rosters if str(r.get('league_id'))==lid]
        if str(l.get('status'))=='pre_draft' or not any(integer(r.get('players_count'))>0 for r in lr):
            result['leagues'][lid]={'league':label,'status':'PREDRAFT','note':'Projected finish activates after rosters exist.'};continue
        try:slots=[s for s in json.loads(l.get('roster_positions') or '[]') if s not in {'BN','IR','TAXI'}]
        except:slots=[]
        strengths={}; details={}
        for r in lr:
            rid=str(r.get('roster_id')); pool=by_roster.get((lid,rid),[]); power,chosen,depth=optimize_lineup(pool,slots)
            inj=sum(1 for p in chosen if str(p.get('injury_status') or '').lower() in {'out','doubtful','questionable'} or str(p.get('injury_note') or ''))
            strengths[rid]=power; details[rid]={'depth_bonus':depth,'injury_flags':inj,'team':r.get('team_name') or r.get('owner_display_name') or f'Roster {rid}','is_my_roster':truth(r.get('is_my_roster'))}
        vals=list(strengths.values()); mean=sum(vals)/len(vals); sd=(sum((x-mean)**2 for x in vals)/max(1,len(vals)))**0.5 or 1.0
        z={rid:(v-mean)/sd for rid,v in strengths.items()}

        playoff_teams=integer(l.get('setting_playoff_teams'),max(4,len(lr)//2)); playoff_start=integer(l.get('setting_playoff_week_start'),15); reg_end=max(week,playoff_start-1)
        pairings=defaultdict(dict)
        for m in matchups:
            if str(m.get('league_id'))!=lid:continue
            w=integer(m.get('week')); mid=str(m.get('matchup_id') or '')
            if not mid or w<week or w>reg_end:continue
            pairings[w].setdefault(mid,[]).append(str(m.get('roster_id')))
        games=[]
        for w,grp in pairings.items():
            for rs in grp.values():
                if len(rs)==2:games.append((w,rs[0],rs[1]))
        future_counts=defaultdict(int); opp_power=defaultdict(list)
        for w,a,b in games:
            future_counts[a]+=1;future_counts[b]+=1;opp_power[a].append(strengths.get(b,mean));opp_power[b].append(strengths.get(a,mean))
        # If Sleeper has not exposed future pairing IDs, fall back to league-average schedule strength.
        expected_remaining=max(0,reg_end-week+1)
        schedule_coverage={rid:min(1.0,future_counts[rid]/expected_remaining) if expected_remaining else 1.0 for rid in strengths}
        schedule_strength={rid:(sum(opp_power[rid])/len(opp_power[rid]) if opp_power[rid] else mean) for rid in strengths}
        sos_pct={rid:pct_rank(list(schedule_strength.values()),schedule_strength[rid]) for rid in strengths}

        base_w={}; base_l={}; base_t={}
        for r in lr:
            rid=str(r.get('roster_id')); wv,lv,tv=record(r);base_w[rid]=wv;base_l[rid]=lv;base_t[rid]=tv
        rng=random.Random(2026*100+week+integer(lid[-4:],0)); finish=defaultdict(list); wins_dist=defaultdict(list); playoff_hits=defaultdict(int)
        rids=list(strengths)
        for _ in range(5000):
            sw={rid:base_w[rid]+0.5*base_t[rid] for rid in rids}
            if games:
                for _,a,b in games:
                    if a not in z or b not in z:continue
                    p=logistic((z[a]-z[b])*0.90)
                    if rng.random()<p:sw[a]+=1
                    else:sw[b]+=1
            else:
                # Low-confidence fallback: simulate remaining games against league-average opposition.
                for rid in rids:
                    p=logistic(z[rid]*0.90)
                    for _g in range(expected_remaining):sw[rid]+=1 if rng.random()<p else 0
            order=sorted(rids,key=lambda rid:(-sw[rid],-strengths[rid],rid))
            for rank,rid in enumerate(order,1):
                finish[rid].append(rank);wins_dist[rid].append(sw[rid]);playoff_hits[rid]+=rank<=playoff_teams

        def quant(xs,q):
            if not xs:return None
            s=sorted(xs); return s[min(len(s)-1,max(0,int((len(s)-1)*q)))]
        league_rows=[]
        for r in lr:
            rid=str(r.get('roster_id')); ranks=finish[rid]; wins=wins_dist[rid]
            row={'league_id':lid,'league':label,'roster_id':rid,'team':details[rid]['team'],'is_my_roster':details[rid]['is_my_roster'],'power_score':round(strengths[rid],2),'power_percentile':pct_rank(vals,strengths[rid]),'league_power_rank':1+sum(v>strengths[rid] for v in vals),'injury_flags':details[rid]['injury_flags'],'depth_bonus':details[rid]['depth_bonus'],'current_wins':base_w[rid],'current_losses':base_l[rid],'expected_wins':round(sum(wins)/len(wins),2),'projected_seed':round(sum(ranks)/len(ranks),1),'most_likely_seed':max(set(ranks),key=ranks.count),'finish_range_low':quant(ranks,.20),'finish_range_high':quant(ranks,.80),'playoff_odds':round(100*playoff_hits[rid]/5000,1),'schedule_strength':round(schedule_strength[rid],2),'schedule_difficulty_percentile':sos_pct[rid],'schedule_label':'HARD' if sos_pct[rid]>=67 else 'EASY' if sos_pct[rid]<=33 else 'AVERAGE','schedule_coverage_pct':round(schedule_coverage[rid]*100,1)}
            rows.append(row);league_rows.append(row)
        mine=next((x for x in league_rows if x['is_my_roster']),None)
        result['leagues'][lid]={'league':label,'status':'ACTIVE','playoff_teams':playoff_teams,'regular_season_end_week':reg_end,'schedule_games_loaded':len(games),'schedule_source':'actual Sleeper H2H' if games else 'league-average fallback','my_outlook':mine,'power_table':sorted(league_rows,key=lambda x:x['league_power_rank'])}
        md.append(f'## {label}')
        if mine:
            md.append(f"- {mine['team']}: projected seed **#{mine['most_likely_seed']}** (average {mine['projected_seed']}); expected wins **{mine['expected_wins']}**; playoff odds **{mine['playoff_odds']}%**")
            md.append(f"- Likely finish range: #{mine['finish_range_low']}–#{mine['finish_range_high']} · roster power rank #{mine['league_power_rank']}/{len(lr)} · schedule {mine['schedule_label']} ({mine['schedule_difficulty_percentile']}th percentile difficulty)")
            md.append(f"- Current injury flags in optimized lineup: {mine['injury_flags']} · future H2H schedule coverage {mine['schedule_coverage_pct']}%")
        md.append('')
    write_csv('projected_finish.csv',rows);replace_table('projected_finish',rows)
    (DATA/'projected_finish.json').write_text(json.dumps(result,indent=2,sort_keys=True),encoding='utf-8');(DATA/'projected_finish.md').write_text('\n'.join(md),encoding='utf-8')
    print(json.dumps({'leagues':len(result['leagues']),'rows':len(rows),'simulations':5000},indent=2))

if __name__=='__main__':main()

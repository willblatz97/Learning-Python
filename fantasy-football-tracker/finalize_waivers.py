from __future__ import annotations
import csv,json,math,sqlite3
from collections import Counter,defaultdict
from pathlib import Path

R=Path(__file__).resolve().parent; O=R/'data'/'normalized'; DB=R/'data'/'fantasy_tracker.sqlite'
def rows(n):
    p=O/n
    return list(csv.DictReader(p.open(encoding='utf-8'))) if p.exists() else []
def truth(v):return str(v).lower() in {'true','1','yes'}
def num(v,d=0):
    try:return float(v)
    except:return d
def lint(v,d=0):
    try:return int(float(v))
    except:return d
def kind(s):
    x=(s or '').lower();return 'chopped' if 'chopped' in x else 'dynasty' if 'dynasty' in x else 'keeper' if 'keeper' in x else 'redraft'
def tier(s):return 'MUST ADD' if s>=85 else 'HIGH' if s>=74 else 'MEDIUM' if s>=62 else 'SPECULATIVE' if s>=50 else 'WATCH'
def band(s,k):
    a=(20,30) if s>=85 else (12,20) if s>=75 else (7,12) if s>=65 else (3,7) if s>=55 else (1,3) if s>=45 else (0,1)
    return (min(50,math.ceil(a[0]*1.4)),min(60,math.ceil(a[1]*1.5))) if k=='chopped' else a
def save(n,data):
    if not data:return
    fs=[];seen=set()
    for r in data:
        for k in r:
            if k not in seen:seen.add(k);fs.append(k)
    with (O/n).open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fs,extrasaction='ignore');w.writeheader();w.writerows(data)
def sql(name,data):
    con=sqlite3.connect(DB)
    try:
        con.execute(f'DROP TABLE IF EXISTS "{name}"')
        if data:
            fs=list(data[0]);defs=', '.join(f'"{c}" TEXT' for c in fs);cols=','.join(f'"{c}"' for c in fs);qs=','.join('?' for _ in fs)
            con.execute(f'CREATE TABLE "{name}" ({defs})');con.executemany(f'INSERT INTO "{name}" ({cols}) VALUES ({qs})',[[None if r.get(c) is None else str(r.get(c)) for c in fs] for r in data])
        con.commit()
    finally:con.close()

def main():
    cfg=json.loads((R/'config.json').read_text()); labels=cfg.get('league_labels',{}); sm=json.loads((R/'data'/'summary.json').read_text())
    leagues={str(r['league_id']):r for r in rows('leagues.csv')}; own=rows('ownership.csv'); adds=rows('waiver_candidates.csv'); cuts=rows('cut_candidates.csv')
    counts=defaultdict(Counter)
    for r in own:
        if truth(r.get('is_my_roster')):counts[str(r['league_id'])][str(r.get('position') or '')]+=1
    for r in adds:
        lid=str(r['league_id']);pos=str(r.get('position') or '');
        try:slots=json.loads(leagues.get(lid,{}).get('roster_positions') or '[]')
        except:slots=[]
        if pos=='QB' and 'SUPER_FLEX' not in slots and counts[lid]['QB']>=1:
            penalty=18 if counts[lid]['QB']>=2 else 10; s=max(0,round(num(r.get('waiver_score'))-penalty,1));r['waiver_score']=s;r['priority']=tier(s)
            reason=str(r.get('reasons') or '').replace('thin QB depth on your roster | ','').replace(' | thin QB depth on your roster','').replace('thin QB depth on your roster','')
            r['reasons']=(reason+' | ' if reason else '')+f"QB room already covered ({counts[lid]['QB']} rostered)"
            lo,hi=band(s,kind(r.get('league')));rem=lint(r.get('faab_remaining'));r['faab_low_pct']=lo;r['faab_high_pct']=hi;r['faab_low']=math.ceil(rem*lo/100);r['faab_high']=math.ceil(rem*hi/100)
    g=defaultdict(list)
    for r in adds:g[str(r['league_id'])].append(r)
    adds=[]
    for lid,x in g.items():
        x.sort(key=lambda r:(-num(r.get('waiver_score')),str(r.get('position') or ''),str(r.get('player') or '')))
        for i,r in enumerate(x[:30],1):r['league_rank']=i;adds.append(r)
    save('waiver_candidates.csv',adds);sql('waiver_candidates',adds)
    active=[str(x) for x in cfg['league_ids'] if leagues.get(str(x),{}).get('status')!='pre_draft'];old=json.loads((R/'data'/'waiver_summary.json').read_text());old['method']=str(old.get('method') or '')+' + roster-context finalizer';old['leagues']={}
    md=[f"# Waiver Board — Week {(sm.get('nfl_state') or {}).get('week')}",'','Consensus-enhanced board. FAAB is a range, not a precise bid.','']
    for lid in active:
        label=labels.get(lid,leagues.get(lid,{}).get('name',lid));la=[r for r in adds if str(r['league_id'])==lid];allc=[r for r in cuts if str(r['league_id'])==lid];rc=[r for r in allc if str(r.get('cut_tier'))!='HOLD'];rem=lint(la[0].get('faab_remaining')) if la else 0;total=lint(leagues.get(lid,{}).get('setting_waiver_budget'),100)
        old['leagues'][lid]={'league':label,'type':kind(label),'faab_remaining':rem,'top_adds':[{k:r.get(k) for k in ['player','position','nfl_team','waiver_score','priority','consensus_ecr','faab_low','faab_high','reasons']} for r in la[:8]],'recommended_cuts':[{k:r.get(k) for k in ['player','position','nfl_team','cut_score','cut_tier','consensus_ecr','reasons']} for r in rc[:6]],'cut_watchlist':[{k:r.get(k) for k in ['player','position','nfl_team','cut_score','cut_tier','consensus_ecr','reasons']} for r in allc[:5]]}
        md += [f'## {label}',f'FAAB remaining: {rem} / {total}','','### Adds']
        for i,r in enumerate(la[:6],1):md.append(f"{i}. {r['player']} ({r['position']} {r['nfl_team']}) — {r['priority']} {r['waiver_score']} — ECR {r.get('consensus_ecr') or 'n/a'} — FAAB {r['faab_low']}-{r['faab_high']} — {r['reasons']}")
        md += ['','### Recommended cuts']
        if rc:
            for i,r in enumerate(rc[:5],1):md.append(f"{i}. {r['player']} ({r['position']} {r['nfl_team']}) — {r['cut_tier']} {r['cut_score']} — {r['reasons']}")
        else:md.append('No recommended cuts from the current roster-value model.')
        md.append('')
    (R/'data'/'waiver_summary.json').write_text(json.dumps(old,indent=2,sort_keys=True));(R/'data'/'waiver_report.md').write_text('\n'.join(md));print(json.dumps({'leagues_finalized':len(active),'qb_counts':{lid:counts[lid]['QB'] for lid in active}},indent=2))
if __name__=='__main__':main()

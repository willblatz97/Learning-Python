from __future__ import annotations
import csv,json,math,sqlite3
from collections import defaultdict
from pathlib import Path

R=Path(__file__).resolve().parent; O=R/'data'/'normalized'; DB=R/'data'/'fantasy_tracker.sqlite'

def rows(n):
    p=O/n
    return list(csv.DictReader(p.open(encoding='utf-8'))) if p.exists() else []
def num(v,d=None):
    try:return float(v)
    except:return d
def lint(v,d=0):
    x=num(v,None); return int(x) if x is not None else d
def kind(label):
    s=label.lower()
    return 'chopped' if 'chopped' in s else 'dynasty' if 'dynasty' in s else 'keeper' if 'keeper' in s else 'redraft'
def elig(slots):
    x={s for s in slots if s in {'QB','RB','WR','TE','K','DEF','DL','LB','DB'}}
    if set(slots)&{'FLEX','REC_FLEX','WRRB_FLEX'}: x|={'RB','WR','TE'}
    if 'SUPER_FLEX' in slots: x|={'QB','RB','WR','TE'}
    return x
def qpts(e):
    if e is None:return 0
    for lim,pts in [(25,36),(50,31),(100,25),(150,19),(200,13),(250,8),(300,4)]:
        if e<=lim:return pts
    return 0
def tier(s):
    return 'MUST ADD' if s>=85 else 'HIGH' if s>=74 else 'MEDIUM' if s>=62 else 'SPECULATIVE' if s>=50 else 'WATCH'
def band(s,k):
    a=(20,30) if s>=85 else (12,20) if s>=75 else (7,12) if s>=65 else (3,7) if s>=55 else (1,3) if s>=45 else (0,1)
    return (min(50,math.ceil(a[0]*1.4)),min(60,math.ceil(a[1]*1.5))) if k=='chopped' else a
def save_csv(n,data):
    if not data:return
    fs=[]; seen=set()
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
            fs=list(data[0]); defs=', '.join(f'"{c}" TEXT' for c in fs); cols=','.join(f'"{c}"' for c in fs); qs=','.join('?' for _ in fs)
            con.execute(f'CREATE TABLE "{name}" ({defs})')
            con.executemany(f'INSERT INTO "{name}" ({cols}) VALUES ({qs})',[[None if r.get(c) is None else str(r.get(c)) for c in fs] for r in data])
        con.commit()
    finally:con.close()

def main():
    cfg=json.loads((R/'config.json').read_text()); sm=json.loads((R/'data'/'summary.json').read_text()); labels=cfg.get('league_labels',{})
    leagues={str(r['league_id']):r for r in rows('leagues.csv')}; ranks=rows('external_rankings.csv'); adds=rows('waiver_candidates.csv'); cuts=rows('cut_candidates.csv')
    lookup={(str(r.get('sleeper_id')),str(r.get('ecr_type'))):r for r in ranks}; slots={}; rtype={}
    for lid,l in leagues.items():
        try:s=json.loads(l.get('roster_positions') or '[]')
        except:s=[]
        slots[lid]=s; k=kind(labels.get(lid,l.get('name',lid))); rtype[lid]='dsf' if k=='dynasty' and 'SUPER_FLEX' in s else 'do' if k=='dynasty' else 'ro'
    A=[]
    for r in adds:
        lid=str(r['league_id']); pos=str(r.get('position') or '')
        if pos not in elig(slots.get(lid,[])):continue
        rr=lookup.get((str(r.get('player_id')),rtype.get(lid,'ro')),{}); e=num(rr.get('ecr')); own=num(rr.get('player_owned_avg'),0) or 0; old=num(r.get('waiver_score'),0) or 0
        s=old*.45+qpts(e)+(6 if own>=60 else 4 if own>=30 else 2 if own>=10 else 0)-(5 if e is None else 0); s=round(max(0,min(100,s)),1); lo,hi=band(s,kind(r.get('league') or '')); rem=lint(r.get('faab_remaining'))
        r.update({'heuristic_score':old,'waiver_score':s,'priority':tier(s),'consensus_ecr':e,'consensus_rank_type':rtype.get(lid),'consensus_owned_avg':own,'consensus_scrape_date':rr.get('scrape_date'),'faab_low_pct':lo,'faab_high_pct':hi,'faab_low':math.ceil(rem*lo/100),'faab_high':math.ceil(rem*hi/100),'quality_source':'FantasyPros consensus via DynastyProcess' if rr else 'heuristic only'})
        if e is not None:r['reasons']=f'consensus ECR {e:g} | '+str(r.get('reasons') or '')
        A.append(r)
    g=defaultdict(list)
    for r in A:g[str(r['league_id'])].append(r)
    A=[]
    for lid,x in g.items():
        x.sort(key=lambda r:(-float(r['waiver_score']),str(r.get('position') or ''),str(r.get('player') or '')))
        for i,r in enumerate(x[:30],1):r['league_rank']=i;A.append(r)
    C=[]
    for r in cuts:
        lid=str(r['league_id']); rr=lookup.get((str(r.get('player_id')),rtype.get(lid,'ro')),{}); e=num(rr.get('ecr')); s=(num(r.get('cut_score'),0) or 0)*.65
        if e is not None:s-=35 if e<=100 else 22 if e<=200 else 10 if e<=300 else 0
        state=str(r.get('roster_state') or ''); k=kind(r.get('league') or '')
        if state=='START':s=min(s,5)
        if state in {'IR','TAXI'}:s=min(s,35)
        if k=='dynasty' and e is not None and e<=300:s=min(s,45)
        s=round(max(0,min(100,s)),1); ct='SAFE CUT' if s>=80 else 'CUTTABLE' if s>=65 else 'ONLY IF NEEDED' if s>=50 else 'HOLD'
        r.update({'cut_score':s,'cut_tier':ct,'consensus_ecr':e,'consensus_rank_type':rtype.get(lid),'consensus_owned_avg':num(rr.get('player_owned_avg')),'consensus_scrape_date':rr.get('scrape_date'),'quality_source':'FantasyPros consensus via DynastyProcess' if rr else 'heuristic only'})
        if e is not None:r['reasons']=f'consensus ECR {e:g} protected | '+str(r.get('reasons') or '')
        C.append(r)
    g=defaultdict(list)
    for r in C:g[str(r['league_id'])].append(r)
    C=[]
    for lid,x in g.items():
        x.sort(key=lambda r:(-float(r['cut_score']),str(r.get('player') or '')))
        for i,r in enumerate(x[:12],1):r['league_rank']=i;C.append(r)
    save_csv('waiver_candidates.csv',A);save_csv('cut_candidates.csv',C);sql('waiver_candidates',A);sql('cut_candidates',C)
    active=[str(x) for x in cfg['league_ids'] if leagues.get(str(x),{}).get('status')!='pre_draft']; result={'snapshot_utc':sm.get('snapshot_utc'),'week':(sm.get('nfl_state') or {}).get('week'),'method':'V1.1 Sleeper + current consensus ECR + depth/injury + transaction momentum + roster need + format','ranking_source':'DynastyProcess ffverse FantasyPros consensus mirror','ranking_scrape_dates':sorted({r.get('scrape_date') for r in ranks if r.get('scrape_date')}),'skipped_predraft_leagues':[str(x) for x in cfg['league_ids'] if str(x) not in active],'leagues':{}}
    md=[f"# Waiver Board — Week {result['week']}",'','Consensus-enhanced V1.1 board. FAAB is a range, not a precise bid.','']
    for lid in active:
        label=labels.get(lid,leagues.get(lid,{}).get('name',lid)); la=[r for r in A if str(r['league_id'])==lid]; lc=[r for r in C if str(r['league_id'])==lid]; rem=lint(la[0].get('faab_remaining')) if la else 0; total=lint(leagues.get(lid,{}).get('setting_waiver_budget'),100)
        result['leagues'][lid]={'league':label,'type':kind(label),'faab_remaining':rem,'top_adds':[{k:r.get(k) for k in ['player','position','nfl_team','waiver_score','priority','consensus_ecr','faab_low','faab_high','reasons']} for r in la[:8]],'top_cuts':[{k:r.get(k) for k in ['player','position','nfl_team','cut_score','cut_tier','consensus_ecr','reasons']} for r in lc[:6]]}
        md += [f'## {label}',f'FAAB remaining: {rem} / {total}','','### Adds']
        for i,r in enumerate(la[:6],1):md.append(f"{i}. {r['player']} ({r['position']} {r['nfl_team']}) — {r['priority']} {r['waiver_score']} — ECR {r.get('consensus_ecr') or 'n/a'} — FAAB {r['faab_low']}-{r['faab_high']} — {r['reasons']}")
        md += ['','### Cuts']
        for i,r in enumerate(lc[:5],1):md.append(f"{i}. {r['player']} ({r['position']} {r['nfl_team']}) — {r['cut_tier']} {r['cut_score']} — ECR {r.get('consensus_ecr') or 'n/a'} — {r['reasons']}")
        md.append('')
    (R/'data'/'waiver_summary.json').write_text(json.dumps(result,indent=2,sort_keys=True));(R/'data'/'waiver_report.md').write_text('\n'.join(md));print(json.dumps({'refined_add_rows':len(A),'refined_cut_rows':len(C),'ranking_dates':result['ranking_scrape_dates']},indent=2))
if __name__=='__main__':main()

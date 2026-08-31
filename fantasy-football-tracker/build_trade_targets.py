from __future__ import annotations

import csv, json, math
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT=Path(__file__).resolve().parent; OUT=ROOT/'data'/'normalized'; RAW=ROOT/'data'/'raw'; CFG=ROOT/'config.json'; SUMMARY=ROOT/'data'/'summary.json'

def read(name):
 p=OUT/name
 if not p.exists(): return []
 with p.open('r',newline='',encoding='utf-8') as f:return list(csv.DictReader(f))

def num(v,d=None):
 try:return float(v)
 except:return d

def truth(v): return str(v).lower() in {'true','1','yes'}
def league_kind(label):
 s=label.lower()
 if 'chopped' in s:return 'chopped'
 if 'dynasty' in s:return 'dynasty'
 if 'keeper' in s:return 'keeper'
 return 'redraft'

def age_of(p):
 a=num(p.get('age'),None)
 if a:return a
 bd=str(p.get('birth_date') or '')
 try:
  d=date.fromisoformat(bd); today=date.today(); return today.year-d.year-((today.month,today.day)<(d.month,d.day))
 except:return None

def roster_record(r):
 try:s=json.loads(r.get('settings') or '{}')
 except:s={}
 w=num(s.get('wins'),0) or 0; l=num(s.get('losses'),0) or 0; t=num(s.get('ties'),0) or 0; g=w+l+t
 return w,l,t,(w+0.5*t)/g if g else 0.5

def rank_score(ecr, ceiling=350):
 e=num(ecr,None)
 if e is None:return 0.0
 return max(0.0, 35.0*(1.0-min(e,ceiling)/ceiling))

def main():
 cfg=json.loads(CFG.read_text(encoding='utf-8')); summary=json.loads(SUMMARY.read_text(encoding='utf-8')); labels=cfg.get('league_labels',{}); week=int((summary.get('nfl_state') or {}).get('week') or 1)
 players=json.loads((RAW/'players_nfl.json').read_text(encoding='utf-8')); leagues={str(r['league_id']):r for r in read('leagues.csv')}; rosters=read('rosters.csv'); ownership=read('ownership.csv'); rankings=read('external_rankings.csv'); scores=read('player_week_scores.csv')
 rank={(str(r.get('sleeper_id')),str(r.get('ecr_type'))):r for r in rankings}; score={(str(r.get('league_id')),str(r.get('player_id'))):r for r in scores}
 roster_by={(str(r['league_id']),str(r['roster_id'])):r for r in rosters}; own_by_l=defaultdict(list)
 for r in ownership: own_by_l[str(r['league_id'])].append(r)
 output={'snapshot_utc':summary.get('snapshot_utc'),'season':(summary.get('nfl_state') or {}).get('season',2026),'week':week,'leagues':{}}
 lines=[f"# Trade Target Board — Week {week}",'', 'Targets are based on roster need, current projection strength, consensus market value, manager record and—where applicable—dynasty age/value curves. They are targets, not assumed accepted offers.','']
 for lid,lrow in leagues.items():
  label=labels.get(lid,lrow.get('name',lid)); kind=league_kind(label)
  if str(lrow.get('status'))=='pre_draft': output['leagues'][lid]={'league':label,'status':'PREDRAFT','push_targets':[],'rebuild_targets':[]}; continue
  if kind=='chopped': output['leagues'][lid]={'league':label,'status':'CHOPPED — trade board suppressed','push_targets':[],'rebuild_targets':[]}; continue
  try: slots=json.loads(lrow.get('roster_positions') or '[]')
  except: slots=[]
  rtype='dsf' if kind=='dynasty' and 'SUPER_FLEX' in slots else 'do' if kind=='dynasty' else 'ro'
  myr=next((r for r in rosters if str(r['league_id'])==lid and truth(r.get('is_my_roster'))),None)
  if not myr: continue
  myrid=str(myr['roster_id']); mw,ml,mt,mwp=roster_record(myr)
  # Current positional strength. Lower top-end score = greater trade need.
  my_scores=defaultdict(list)
  for r in own_by_l[lid]:
   if str(r.get('roster_id'))!=myrid: continue
   s=score.get((lid,str(r.get('player_id'))),{}); my_scores[str(r.get('position') or '')].append(num(s.get('lineup_score'),0) or 0)
  need={}
  targets_needed={'QB':1,'RB':2,'WR':3,'TE':1}
  for pos,n in targets_needed.items():
   vals=sorted(my_scores.get(pos,[]),reverse=True)[:n]; avg=sum(vals)/len(vals) if vals else 0; need[pos]=max(0.0,min(12.0,(20.0-avg)*0.8))
  candidates=[]
  for r in own_by_l[lid]:
   if truth(r.get('is_my_roster')): continue
   pos=str(r.get('position') or '')
   if pos not in {'QB','RB','WR','TE'}: continue
   pid=str(r.get('player_id')); owner_r=roster_by.get((lid,str(r.get('roster_id'))),{}); ow,ol,ot,owp=roster_record(owner_r)
   p=players.get(pid,{}); age=age_of(p); rr=rank.get((pid,rtype),{}); red=rank.get((pid,'ro'),{}); dyn=rank.get((pid,'dsf'),{}) or rank.get((pid,'do'),{})
   ecr=num(rr.get('ecr'),None); red_ecr=num(red.get('ecr'),None); dyn_ecr=num(dyn.get('ecr'),None); sc=num(score.get((lid,pid),{}).get('lineup_score'),0) or 0
   seller=owner_r.get('team_name') or owner_r.get('owner_display_name') or f"Roster {r.get('roster_id')}"
   immediate=rank_score(red_ecr)+min(sc,25)*1.3+need.get(pos,0)
   if owp<0.4: immediate+=4
   if age is not None and ((pos=='RB' and age>=27) or (pos in {'WR','TE'} and age>=29) or (pos=='QB' and age>=32)): immediate+=2
   push_reasons=[f"{pos} need +{need.get(pos,0):.1f}", f"lineup score {sc:.1f}"]
   if red_ecr is not None: push_reasons.append(f"redraft ECR {red_ecr:.1f}")
   if owp<0.4: push_reasons.append(f"seller currently {ow:.0f}-{ol:.0f}")
   rebuild=-999
   rebuild_reasons=[]
   if kind=='dynasty':
    youth=0
    if age is not None:
     youth=max(0,10-(age-21)*1.3) if pos!='QB' else max(0,10-(age-23)*0.8)
    arb=0
    if dyn_ecr is not None and red_ecr is not None: arb=max(-5,min(12,(red_ecr-dyn_ecr)*0.06))
    rebuild=rank_score(dyn_ecr)+youth+arb+(3 if owp>0.6 else 0)+need.get(pos,0)*0.25
    rebuild_reasons=[f"age {age:.0f}" if age is not None else 'age unknown']
    if dyn_ecr is not None: rebuild_reasons.append(f"dynasty ECR {dyn_ecr:.1f}")
    if arb>2: rebuild_reasons.append('dynasty value exceeds current redraft value')
    if owp>0.6: rebuild_reasons.append(f"contending seller {ow:.0f}-{ol:.0f} may prefer current production")
   candidates.append({'player_id':pid,'player':r.get('full_name'),'position':pos,'nfl_team':r.get('nfl_team'),'age':round(age,1) if age is not None else None,'seller':seller,'seller_roster_id':r.get('roster_id'),'seller_record':f"{int(ow)}-{int(ol)}",'push_score':round(immediate,1),'push_reasons':' | '.join(push_reasons),'rebuild_score':round(rebuild,1) if rebuild>-900 else None,'rebuild_reasons':' | '.join(rebuild_reasons),'redraft_ecr':red_ecr,'dynasty_ecr':dyn_ecr,'current_lineup_score':round(sc,2)})
  push=sorted(candidates,key=lambda x:-x['push_score'])[:8]; rebuild=sorted([x for x in candidates if x['rebuild_score'] is not None],key=lambda x:-x['rebuild_score'])[:8]
  if kind=='dynasty' and week>=5 and mwp<=0.35: posture='REBUILD CANDIDATE'
  elif week>=5 and mwp>=0.6: posture='PLAYOFF PUSH'
  else: posture='DUAL TRACK — evaluate push and rebuild' if kind=='dynasty' else 'PLAYOFF / RECOVERY PUSH'
  output['leagues'][lid]={'league':label,'format':kind,'my_record':f"{int(mw)}-{int(ml)}",'posture':posture,'position_need':{k:round(v,1) for k,v in need.items()},'push_targets':push,'rebuild_targets':rebuild}
  lines += [f"## {label}",f"- Posture: **{posture}** — record {int(mw)}-{int(ml)}",f"- Push targets: "+('; '.join(f"{x['player']} ({x['position']}, {x['seller']})" for x in push[:5]) if push else 'none')]
  if kind=='dynasty': lines.append('- Rebuild targets: '+('; '.join(f"{x['player']} ({x['position']}, age {x['age']}, {x['seller']})" for x in rebuild[:5]) if rebuild else 'none'))
  lines.append('')
 (ROOT/'data'/'trade_targets.json').write_text(json.dumps(output,indent=2,sort_keys=True),encoding='utf-8'); (ROOT/'data'/'trade_targets.md').write_text('\n'.join(lines),encoding='utf-8')
 print(json.dumps({'leagues_analyzed':len(output['leagues']),'week':week},indent=2))
if __name__=='__main__': main()

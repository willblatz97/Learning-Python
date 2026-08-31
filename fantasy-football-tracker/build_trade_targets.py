from __future__ import annotations
import csv,json
from collections import defaultdict
from datetime import date
from pathlib import Path
ROOT=Path(__file__).resolve().parent; OUT=ROOT/'data'/'normalized'; RAW=ROOT/'data'/'raw'; CFG=ROOT/'config.json'; SUMMARY=ROOT/'data'/'summary.json'
def read(n):
 p=OUT/n
 if not p.exists():return []
 with p.open('r',newline='',encoding='utf-8') as f:return list(csv.DictReader(f))
def num(v,d=None):
 try:return float(v)
 except:return d
def truth(v):return str(v).lower() in {'true','1','yes'}
def kind(s):
 s=s.lower()
 return 'chopped' if 'chopped' in s else 'dynasty' if 'dynasty' in s else 'keeper' if 'keeper' in s else 'redraft'
def age(p):
 a=num(p.get('age'),None)
 if a:return a
 try:
  d=date.fromisoformat(str(p.get('birth_date'))); t=date.today(); return t.year-d.year-((t.month,t.day)<(d.month,d.day))
 except:return None
def rec(r):
 try:s=json.loads(r.get('settings') or '{}')
 except:s={}
 w=num(s.get('wins'),0) or 0;l=num(s.get('losses'),0) or 0;t=num(s.get('ties'),0) or 0;g=w+l+t
 return w,l,(w+.5*t)/g if g else .5
def rscore(e,ceil=350):
 e=num(e,None)
 return 0 if e is None else max(0,35*(1-min(e,ceil)/ceil))
def cost_fit(e):
 e=num(e,None)
 if e is None:return -3
 if e<=12:return -18
 if e<=24:return -10
 if e<=50:return -4
 if e<=120:return 5
 if e<=200:return 2
 return -2
def diverse(rows,key,limit=8,max_pos=2):
 out=[]; c=defaultdict(int)
 for x in sorted(rows,key=lambda z:-(z.get(key) or -999)):
  if c[x['position']]>=max_pos:continue
  out.append(x);c[x['position']]+=1
  if len(out)>=limit:break
 return out
def main():
 cfg=json.loads(CFG.read_text()); summ=json.loads(SUMMARY.read_text()); labels=cfg.get('league_labels',{}); week=int((summ.get('nfl_state') or {}).get('week') or 1)
 players=json.loads((RAW/'players_nfl.json').read_text()); leagues={str(r['league_id']):r for r in read('leagues.csv')}; rosters=read('rosters.csv'); own=read('ownership.csv'); ranks=read('external_rankings.csv'); scores=read('player_week_scores.csv')
 rank={(str(r.get('sleeper_id')),str(r.get('ecr_type'))):r for r in ranks}; score={(str(r.get('league_id')),str(r.get('player_id'))):r for r in scores}; rb={(str(r['league_id']),str(r['roster_id'])):r for r in rosters}; by=defaultdict(list)
 for r in own:by[str(r['league_id'])].append(r)
 out={'snapshot_utc':summ.get('snapshot_utc'),'season':2026,'week':week,'leagues':{}}; lines=[f'# Trade Target Board — Week {week}','', 'This board favors actionable value: roster need + seller surplus + market arbitrage + attainability. Elite players can still appear, but expensive top-12 assets are deliberately penalized so the list does not become a generic superstar ranking.','']
 for lid,l in leagues.items():
  label=labels.get(lid,l.get('name',lid)); k=kind(label)
  if str(l.get('status'))=='pre_draft':out['leagues'][lid]={'league':label,'status':'PREDRAFT','push_targets':[],'rebuild_targets':[]};continue
  if k=='chopped':out['leagues'][lid]={'league':label,'status':'CHOPPED — trade board suppressed','push_targets':[],'rebuild_targets':[]};continue
  try:slots=json.loads(l.get('roster_positions') or '[]')
  except:slots=[]
  sf='SUPER_FLEX' in slots; rt='dsf' if k=='dynasty' and sf else 'do' if k=='dynasty' else 'ro'; my=next((r for r in rosters if str(r['league_id'])==lid and truth(r.get('is_my_roster'))),None)
  if not my:continue
  mrid=str(my['roster_id']);mw,ml,mwp=rec(my); mys=defaultdict(list)
  for r in by[lid]:
   if str(r.get('roster_id'))==mrid:mys[str(r.get('position') or '')].append(num(score.get((lid,str(r.get('player_id'))),{}).get('lineup_score'),0) or 0)
  need={}; req={'QB':2 if sf else 1,'RB':2,'WR':3,'TE':1}
  for p,n in req.items():
   vals=sorted(mys.get(p,[]),reverse=True)[:n]; avg=sum(vals)/len(vals) if vals else 0;need[p]=max(0,min(12,(20-avg)*.8))
  # Seller positional surplus among reasonably marketable assets.
  surplus=defaultdict(lambda:defaultdict(int))
  thresholds={'QB':2 if sf else 1,'RB':3,'WR':4,'TE':2}
  for r in by[lid]:
   if truth(r.get('is_my_roster')):continue
   pid=str(r.get('player_id'));p=str(r.get('position') or '');re=num(rank.get((pid,'ro'),{}).get('ecr'),999)
   if p in thresholds and re<=180:surplus[str(r.get('roster_id'))][p]+=1
  cand=[]
  for r in by[lid]:
   if truth(r.get('is_my_roster')):continue
   p=str(r.get('position') or '')
   if p not in req:continue
   pid=str(r.get('player_id')); rr=rank.get((pid,rt),{}); red=rank.get((pid,'ro'),{}); dyn=rank.get((pid,'dsf'),{}) or rank.get((pid,'do'),{}); re=num(red.get('ecr'),None); de=num(dyn.get('ecr'),None); sc=num(score.get((lid,pid),{}).get('lineup_score'),0) or 0;pr=players.get(pid,{}); a=age(pr); orid=str(r.get('roster_id')); owner=rb.get((lid,orid),{});ow,ol,owp=rec(owner); seller=owner.get('team_name') or owner.get('owner_display_name') or f'Roster {orid}'
   sur=max(0,surplus[orid][p]-thresholds[p]); sur_bonus=min(8,sur*3); winarb=0
   if k=='dynasty' and re is not None and de is not None:winarb=max(-4,min(12,(de-re)*.08))
   push=rscore(re)+min(sc,25)*1.15+need[p]+cost_fit(re)+sur_bonus+winarb+(4 if week>=4 and owp<.4 else 0)
   reasons=[f'{p} need {need[p]:.1f}',f'weekly value {sc:.1f}']
   if re is not None:reasons.append(f'redraft ECR {re:.1f}')
   if sur:reasons.append(f'seller has {sur} extra marketable {p}(s)')
   if winarb>3:reasons.append('win-now value exceeds dynasty cost')
   if re is not None and re<=24:reasons.append('elite-cost target; price discipline required')
   rebuild=None;rreasons=[]
   if k=='dynasty':
    youth=0 if a is None else max(0,11-(a-(23 if p=='QB' else 21))*(.8 if p=='QB' else 1.25)); arb=0 if re is None or de is None else max(-5,min(14,(re-de)*.08)); rebuild=rscore(de)+youth+arb+sur_bonus*.5+(3 if week>=4 and owp>.6 else 0)+need[p]*.2
    rreasons=[f'age {a:.0f}' if a is not None else 'age unknown']
    if de is not None:rreasons.append(f'dynasty ECR {de:.1f}')
    if arb>3:rreasons.append('dynasty value exceeds current redraft production')
    if sur:rreasons.append('seller positional surplus creates a negotiation path')
   cand.append({'player_id':pid,'player':r.get('full_name'),'position':p,'nfl_team':r.get('nfl_team'),'age':round(a,1) if a is not None else None,'seller':seller,'seller_roster_id':orid,'seller_record':f'{int(ow)}-{int(ol)}','seller_surplus':sur,'push_score':round(push,1),'push_reasons':' | '.join(reasons),'rebuild_score':round(rebuild,1) if rebuild is not None else None,'rebuild_reasons':' | '.join(rreasons),'redraft_ecr':re,'dynasty_ecr':de,'current_lineup_score':round(sc,2)})
  push=diverse(cand,'push_score');reb=diverse([x for x in cand if x['rebuild_score'] is not None],'rebuild_score')
  posture='REBUILD CANDIDATE' if k=='dynasty' and week>=5 and mwp<=.35 else 'PLAYOFF PUSH' if week>=5 and mwp>=.6 else 'DUAL TRACK — evaluate push and rebuild' if k=='dynasty' else 'PLAYOFF / RECOVERY PUSH'
  out['leagues'][lid]={'league':label,'format':k,'my_record':f'{int(mw)}-{int(ml)}','posture':posture,'position_need':{p:round(v,1) for p,v in need.items()},'push_targets':push,'rebuild_targets':reb}
  lines += [f'## {label}',f'- Posture: **{posture}** — record {int(mw)}-{int(ml)}', '- Push targets: '+('; '.join(f"{x['player']} ({x['position']}, {x['seller']})" for x in push[:5]) or 'none')]
  if k=='dynasty':lines.append('- Rebuild targets: '+('; '.join(f"{x['player']} ({x['position']}, age {x['age']}, {x['seller']})" for x in reb[:5]) or 'none'))
  lines.append('')
 (ROOT/'data'/'trade_targets.json').write_text(json.dumps(out,indent=2,sort_keys=True));(ROOT/'data'/'trade_targets.md').write_text('\n'.join(lines));print(json.dumps({'leagues_analyzed':len(out['leagues']),'week':week},indent=2))
if __name__=='__main__':main()

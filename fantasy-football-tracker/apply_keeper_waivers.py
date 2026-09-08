from __future__ import annotations
import csv,json,math,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parent; OUT=ROOT/'data'/'normalized'; DB=ROOT/'data'/'fantasy_tracker.sqlite'; LID='1359546418284494848'
def read(n):
 p=OUT/n
 if not p.exists():return []
 with p.open('r',newline='',encoding='utf-8') as f:return list(csv.DictReader(f))
def num(v,d=None):
 try:return float(v)
 except:return d
def write(n,rs):
 if not rs:return
 fs=[];seen=set()
 for r in rs:
  for k in r:
   if k not in seen:seen.add(k);fs.append(k)
 with (OUT/n).open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fs,extrasaction='ignore');w.writeheader();w.writerows(rs)
def repl(n,rs):
 con=sqlite3.connect(DB)
 try:
  con.execute(f'DROP TABLE IF EXISTS "{n}"')
  if rs:
   fs=list(rs[0]);defs=', '.join(chr(34)+c+chr(34)+' TEXT' for c in fs);cols=','.join(chr(34)+c+chr(34) for c in fs);qs=','.join('?' for _ in fs);con.execute(f'CREATE TABLE "{n}" ({defs})');con.executemany(f'INSERT INTO "{n}" ({cols}) VALUES ({qs})',[[None if r.get(c) is None else str(r.get(c)) for c in fs] for r in rs])
  con.commit()
 finally:con.close()
def tier(s):return 'MUST ADD' if s>=85 else 'HIGH' if s>=74 else 'MEDIUM' if s>=62 else 'SPECULATIVE' if s>=50 else 'WATCH'
def main():
 leagues={str(r.get('league_id')):r for r in read('leagues.csv')}
 if leagues.get(LID,{}).get('status')=='pre_draft':
  print(json.dumps({'keeper_waiver_adjustments':0,'status':'PREDRAFT'},indent=2));return
 rules=json.loads((ROOT/'keeper_rules.json').read_text())[LID];fa_round=int(rules.get('free_agent_keeper_round',7));eligible=set(rules.get('keeper_eligible_positions') or ['QB','RB','WR','TE']);adds=read('waiver_candidates.csv');cuts=read('cut_candidates.csv');avail=read('availability_matrix.csv');ranks=read('external_rankings.csv');idp={str(r.get('sleeper_id')):r for r in read('idp_values.csv')};kv={str(r.get('player_id')):r for r in read('keeper_values.csv')}
 ro={str(r.get('sleeper_id')):r for r in ranks if str(r.get('ecr_type'))=='ro'}; existing={(str(r.get('league_id')),str(r.get('player_id'))):r for r in adds};keeper_bonuses=0;idp_rows=0
 for a in avail:
  if a.get('league_'+LID)!='FA':continue
  pid=str(a.get('player_id') or '');pos=str(a.get('position') or '')
  if pos not in {'QB','RB','WR','TE','K','DL','LB','DB'}:continue
  old=existing.get((LID,pid));score=num((old or {}).get('waiver_score'),0) or 0;reasons=[];market=None;surplus=0
  # Keeper value is a small offensive tiebreaker only. Never create a waiver target solely from keeper surplus.
  if pos in eligible and old is not None:
   rr=ro.get(pid,{});e=num(rr.get('ecr'),None);market=max(1,min(17,int(math.ceil(e/12)))) if e else None;surplus=max(0,(fa_round-market) if market else 0)
   if surplus>0:
    bonus=min(5.0,surplus*0.75);score+=bonus;keeper_bonuses+=1;reasons.append(f'keeper tiebreaker +{bonus:.1f}: FA R{fa_round} vs market R{market}')
  # IDP is redraft-only: current scoring proxy may create/boost a target, with zero keeper value.
  if pos in {'DL','LB','DB'} and pid in idp:
   proxy=num(idp[pid].get('idp_projection_proxy'),0) or 0;score=max(score,min(95,30+proxy*4));reasons.append(f'current-season IDP value proxy {proxy:.1f} pts/g; keeper value ignored');idp_rows+=1
  if old is None and pos not in {'DL','LB','DB'}:continue
  if old is None and not reasons:continue
  score=round(max(0,min(100,score)),1);lo,hi=((12,20) if score>=75 else (7,12) if score>=65 else (3,7) if score>=55 else (1,3) if score>=45 else (0,1));rem=int(num((old or {}).get('faab_remaining'),100) or 100)
  row=dict(old or {'league_id':LID,'league':rules['league'],'league_type':'keeper','player_id':pid,'player':a.get('full_name'),'position':pos,'nfl_team':a.get('nfl_team'),'faab_remaining':rem,'depth_chart_order':a.get('depth_chart_order'),'injury_status':a.get('injury_status')})
  row.update({'waiver_score':score,'priority':tier(score),'faab_low_pct':lo,'faab_high_pct':hi,'faab_low':math.ceil(rem*lo/100),'faab_high':math.ceil(rem*hi/100),'keeper_cost_if_added':fa_round if pos in eligible else None,'estimated_market_round':market if pos in eligible else None,'keeper_round_surplus':surplus if pos in eligible else 0})
  if reasons:row['reasons']=(str(row.get('reasons') or '')+' | '+' | '.join(reasons)).strip(' |')
  existing[(LID,pid)]=row
 other=[r for r in adds if str(r.get('league_id'))!=LID];ka=[r for (lid,pid),r in existing.items() if lid==LID];ka.sort(key=lambda r:-num(r.get('waiver_score'),0));
 for i,r in enumerate(ka[:30],1):r['league_rank']=i
 adds=other+ka[:30]
 cut_adjustments=0
 for r in cuts:
  if str(r.get('league_id'))!=LID:continue
  pos=str(r.get('position') or '')
  if pos not in eligible:continue
  v=kv.get(str(r.get('player_id')),{});sur=max(0,num(v.get('round_surplus'),0) or 0)
  if sur>0:
   before=num(r.get('cut_score'),0) or 0;protection=min(5.0,sur*0.75);r['cut_score_before_keeper']=before;r['cut_score']=round(max(0,before-protection),1);r['keeper_round_surplus']=sur;r['keeper_round']=v.get('keeper_round');r['reasons']=str(r.get('reasons') or '')+f' | keeper tiebreaker only: -{protection:.1f} cut points';cut_adjustments+=1
 write('waiver_candidates.csv',adds);write('cut_candidates.csv',cuts);repl('waiver_candidates',adds);repl('cut_candidates',cuts);print(json.dumps({'keeper_waiver_candidates':len(ka),'keeper_tiebreaker_bonuses':keeper_bonuses,'keeper_cut_tiebreakers':cut_adjustments,'idp_redraft_rows':idp_rows,'keeper_policy':'redraft first; offense-only tiebreaker; IDP keeper value disabled'},indent=2))
if __name__=='__main__':main()

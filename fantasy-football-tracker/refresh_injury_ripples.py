from __future__ import annotations
import csv,gzip,io,json,sqlite3
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parent; OUT=ROOT/'data'/'normalized'; RAW=ROOT/'data'/'raw'; DB=ROOT/'data'/'fantasy_tracker.sqlite'; SEASON=2026
DEPTH=f'https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_{SEASON}.csv.gz'; ROSTER=f'https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_{SEASON}.csv'
FIX={'JAC':'JAX','WSH':'WAS','LA':'LAR','OAK':'LV','SD':'LAC','STL':'LAR'}
def tf(v):
 x=str(v or '').strip().upper();return FIX.get(x,x)
def rows(url,gz=False):
 req=Request(url,headers={'User-Agent':'blatzzy-fantasy-tracker/1.0'});raw=urlopen(req,timeout=90).read();raw=gzip.decompress(raw) if gz else raw;return list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig',errors='replace'))))
def rcsv(n):
 p=OUT/n
 if not p.exists():return []
 with p.open('r',newline='',encoding='utf-8') as f:return list(csv.DictReader(f))
def wcsv(n,rs):
 p=OUT/n
 if not rs:p.write_text('',encoding='utf-8');return
 fs=[];seen=set()
 for r in rs:
  for k in r:
   if k not in seen:seen.add(k);fs.append(k)
 with p.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fs,extrasaction='ignore');w.writeheader();w.writerows(rs)
def repl(n,rs):
 con=sqlite3.connect(DB)
 try:
  con.execute(f'DROP TABLE IF EXISTS "{n}"')
  if rs:
   fs=list(rs[0]);con.execute(f'CREATE TABLE "{n}" ({", ".join(chr(34)+c+chr(34)+" TEXT" for c in fs)})');qs=','.join('?' for _ in fs);cols=','.join(chr(34)+c+chr(34) for c in fs);con.executemany(f'INSERT INTO "{n}" ({cols}) VALUES ({qs})',[[None if r.get(c) is None else str(r.get(c)) for c in fs] for r in rs])
  con.commit()
 finally:con.close()
def nn(v):return ''.join(ch.lower() for ch in str(v or '') if ch.isalnum())
def integer(v,d=99):
 try:return int(float(v))
 except:return d
def role(r):
 a=str(r.get('pos_abb') or '').upper().strip();n=str(r.get('pos_name') or '').upper();b=f'{a} {n} {r.get("pos_grp") or ""}'.upper()
 if a in {'QB','RB','WR','TE','CB','S','FS','SS','LB','ILB','OLB','DE','DT','NT','EDGE'}:return a
 if 'CORNER' in b:return 'CB'
 if 'SAFETY' in b:return 'S'
 if 'LINEBACK' in b:return 'LB'
 if 'DEFENSIVE END' in b or 'EDGE' in b:return 'EDGE'
 if 'DEFENSIVE TACKLE' in b or 'NOSE TACKLE' in b:return 'DT'
 return a
def risk(p,official):
 inj=str(p.get('injury_status') or '').lower();practice=' | '.join(str(p.get(k) or '').lower() for k in ['practice_participation','practice_description'])
 if any(x in inj for x in ['injured reserve','reserve/injured','pup','physically unable']) or inj=='out':return 1.0,'OUT/IR'
 if 'doubt' in inj:return .8,'DOUBTFUL'
 soft=.45 if official else .20;limited=.25 if official else .10
 if 'question' in inj or 'did not' in practice or 'dnp' in practice:return soft,'QUESTIONABLE/DNP'
 if 'limited' in practice:return limited,'LIMITED'
 return 0.0,'HEALTHY'
def main():
 ts=datetime.now(timezone.utc).replace(microsecond=0).isoformat();players=json.loads((RAW/'players_nfl.json').read_text());depth=rows(DEPTH,True);rost=rows(ROSTER);ctx={tf(r.get('team')):r for r in rcsv('team_week_context.csv')}
 try:official=bool(json.loads((ROOT/'data'/'ol_health_summary.json').read_text()).get('official_injury_report_available'))
 except:official=False
 gs,ep={},{}
 for r in rost:
  sid=str(r.get('sleeper_id') or '').strip()
  if not sid or sid in {'NA','None'}:continue
  g=str(r.get('gsis_id') or '').strip();e=str(r.get('espn_id') or '').strip()
  if g and g not in {'NA','None'}:gs[g]=sid
  if e and e not in {'NA','None'}:ep[e]=sid
 nt={}
 for sid,p in players.items():
  t=tf(p.get('team'));n=nn(p.get('full_name') or f"{p.get('first_name','')} {p.get('last_name','')}")
  if t and n:nt[(t,n)]=str(sid)
 mx={}
 for r in depth:
  t=tf(r.get('team'));d=str(r.get('dt') or '')
  if t and (t not in mx or d>mx[t]):mx[t]=d
 latest=[r for r in depth if str(r.get('dt') or '')==mx.get(tf(r.get('team')))]
 by=defaultdict(list)
 for r in latest:
  t=tf(r.get('team'));sid=gs.get(str(r.get('gsis_id') or '').strip()) or ep.get(str(r.get('espn_id') or '').strip()) or nt.get((t,nn(r.get('player_name'))));rr=dict(r);rr.update(team_clean=t,sid=sid,role=role(r),rank=integer(r.get('pos_rank')))
  if sid and rr['role']:by[(t,rr['role'])].append(rr)
 for k in by:by[k].sort(key=lambda x:x['rank'])
 def nth(t,roles,n=0):
  pool=[]
  for rr in roles:pool+=by.get((t,rr),[])
  pool.sort(key=lambda x:x['rank']);return pool[n] if len(pool)>n else None
 ev=[]
 def add(tar,src,pts,why,side,conf='MEDIUM'):
  if not tar or not src or not tar.get('sid') or abs(pts)<.01:return
  tp=players.get(str(tar['sid']),{});sp=players.get(str(src['sid']),{});ev.append({'snapshot_utc':ts,'target_sleeper_id':tar['sid'],'target_player':tp.get('full_name') or tar.get('player_name'),'target_team':tar['team_clean'],'target_position':tar['role'],'source_sleeper_id':src['sid'],'source_player':sp.get('full_name') or src.get('player_name'),'source_team':src['team_clean'],'source_position':src['role'],'ripple_points':round(pts,2),'side':side,'confidence':conf,'reason':why})
 teams=sorted(set(t for t,_ in by))
 for team in teams:
  opp=tf((ctx.get(team) or {}).get('opponent'))
  for rn in ['QB','RB','WR','TE']:
   s=nth(team,[rn],0)
   if not s:continue
   rr,lab=risk(players.get(str(s['sid']),{}),official)
   if rr<=0:continue
   if rn=='RB':add(nth(team,['RB'],1),s,1.55*rr,f'RB1 {lab}: backfield opportunity moves to RB2','OFFENSE','HIGH');add(nth(team,['RB'],2),s,.45*rr,f'RB1 {lab}: secondary backfield opportunity','OFFENSE')
   elif rn=='WR':add(nth(team,['WR'],1),s,1.10*rr,f'WR1 {lab}: target concentration rises for next WR','OFFENSE','HIGH');add(nth(team,['WR'],2),s,.55*rr,f'WR1 {lab}: WR3 role/targets can expand','OFFENSE');add(nth(team,['TE'],0),s,.50*rr,f'WR1 {lab}: TE target share can rise','OFFENSE')
   elif rn=='TE':add(nth(team,['TE'],1),s,.85*rr,f'TE1 {lab}: TE2 route/target opportunity rises','OFFENSE','HIGH');add(nth(team,['WR'],0),s,.25*rr,f'TE1 {lab}: WR1 target share can rise','OFFENSE')
   else:
    add(nth(team,['QB'],1),s,1.20*rr,f'QB1 {lab}: QB2 becomes relevant','OFFENSE','HIGH')
    for tar in [nth(team,['WR'],0),nth(team,['WR'],1),nth(team,['TE'],0)]:add(tar,s,-.45*rr,f'QB1 {lab}: pass-catcher efficiency risk with backup QB','OFFENSE')
  if not opp:continue
  for roles,b in [(['CB'],'CB'),(['S','FS','SS'],'S'),(['LB','ILB','OLB'],'LB'),(['EDGE','DE'],'EDGE'),(['DT','NT'],'DT')]:
   s=nth(team,roles,0)
   if not s:continue
   rr,lab=risk(players.get(str(s['sid']),{}),official)
   if rr<=0:continue
   if b=='CB':add(nth(opp,['WR'],0),s,1.0*rr,f'Opponent CB1 {lab}: projected WR1 coverage matchup improves (not confirmed shadow)','DEFENSE');add(nth(opp,['WR'],1),s,.4*rr,f'Opponent CB1 {lab}: secondary WR matchup improves','DEFENSE')
   elif b=='S':add(nth(opp,['WR'],0),s,.4*rr,f'Starting safety {lab}: downfield coverage improves','DEFENSE');add(nth(opp,['TE'],0),s,.5*rr,f'Starting safety {lab}: TE middle/deep matchup improves','DEFENSE')
   elif b=='LB':add(nth(opp,['TE'],0),s,.55*rr,f'Starting linebacker {lab}: TE coverage improves','DEFENSE');add(nth(opp,['RB'],0),s,.35*rr,f'Starting linebacker {lab}: RB run/checkdown environment improves','DEFENSE')
   elif b=='EDGE':add(nth(opp,['QB'],0),s,.45*rr,f'Top edge rusher {lab}: QB pressure environment improves','DEFENSE');add(nth(opp,['WR'],0),s,.15*rr,f'Top edge rusher {lab}: more time helps primary WR routes','DEFENSE','LOW')
   else:add(nth(opp,['RB'],0),s,.45*rr,f'Starting interior DL {lab}: rushing matchup improves','DEFENSE')
 agg={}
 for r in ev:
  sid=str(r['target_sleeper_id']);a=agg.setdefault(sid,{'points':0.0,'reasons':[],'high':False,'team':r['target_team'],'pos':r['target_position'],'player':r['target_player']});a['points']+=float(r['ripple_points']);a['reasons'].append(r['reason']);a['high']=a['high'] or r['confidence']=='HIGH'
 prs=[]
 for sid,a in agg.items():prs.append({'snapshot_utc':ts,'sleeper_id':sid,'player':a['player'],'team':a['team'],'position':a['pos'],'injury_ripple_points':round(max(-2.25,min(2.25,a['points'])),2),'confidence':'HIGH' if a['high'] else 'MEDIUM','reasons':' | '.join(a['reasons'])})
 prs.sort(key=lambda r:(-float(r['injury_ripple_points']),r.get('player') or ''));wcsv('injury_ripples.csv',ev);wcsv('injury_ripple_players.csv',prs);repl('injury_ripples',ev);repl('injury_ripple_players',prs)
 summary={'snapshot_utc':ts,'official_injury_reports':official,'soft_flag_weight':.45 if official else .20,'raw_ripple_events':len(ev),'players_affected':len(prs),'positive_players':sum(float(r['injury_ripple_points'])>0 for r in prs),'negative_players':sum(float(r['injury_ripple_points'])<0 for r in prs),'max_player_adjustment':2.25,'note':'Soft Questionable/DNP/Limited signals are damped until official weekly injury reports exist. Defensive CB1-to-WR1 is matchup leverage, not a confirmed shadow assignment.'};(ROOT/'data'/'injury_ripple_summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True))
 lines=['# Injury Ripple Intelligence','',summary['note'],'','## Biggest current positive ripples']
 for r in prs[:15]:
  if float(r['injury_ripple_points'])<=0:break
  lines.append(f"- {r['player']} ({r['team']} {r['position']}) {float(r['injury_ripple_points']):+.2f} — {r['reasons']}")
 (ROOT/'data'/'injury_ripple_report.md').write_text('\n'.join(lines));print(json.dumps(summary,indent=2))
if __name__=='__main__':main()

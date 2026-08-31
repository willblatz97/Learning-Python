from __future__ import annotations
import csv,json,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parent; OUT=ROOT/'data'/'normalized'; DB=ROOT/'data'/'fantasy_tracker.sqlite'; LID='1359546418284494848'
def read(n):
 p=OUT/n
 if not p.exists(): return []
 with p.open('r',newline='',encoding='utf-8') as f:return list(csv.DictReader(f))
def num(v,d=0.0):
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
   fs=list(rs[0]);con.execute(f'CREATE TABLE "{n}" ({", ".join(fchr for fchr in [chr(34)+c+chr(34)+" TEXT" for c in fs])})');qs=','.join('?' for _ in fs);cols=','.join(chr(34)+c+chr(34) for c in fs);con.executemany(f'INSERT INTO "{n}" ({cols}) VALUES ({qs})',[[None if r.get(c) is None else str(r.get(c)) for c in fs] for r in rs])
  con.commit()
 finally:con.close()
def main():
 scores=read('player_week_scores.csv'); vals={str(r.get('sleeper_id')):r for r in read('idp_values.csv')};changed=0
 for r in scores:
  if str(r.get('league_id'))!=LID or str(r.get('position')) not in {'DL','LB','DB'}:continue
  v=vals.get(str(r.get('player_id')))
  if not v:continue
  base=num(v.get('idp_projection_proxy'));r['base_lineup_score_before_idp']=r.get('lineup_score');r['lineup_score']=round(max(0,base),2);r['idp_points_applied']=round(num(r['lineup_score'])-num(r.get('base_lineup_score_before_idp')),2);r['idp_source_season']=v.get('source_season');r['idp_current_season_data']=v.get('current_season_data');r['score_source']='league-specific IDP scoring proxy';changed+=1
 write('player_week_scores.csv',scores);repl('player_week_scores',scores);print(json.dumps({'idp_player_scores_replaced':changed,'league_id':LID},indent=2))
if __name__=='__main__':main()

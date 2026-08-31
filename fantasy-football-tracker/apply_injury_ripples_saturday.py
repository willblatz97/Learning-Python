from __future__ import annotations
import csv, json, sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parent; OUT=ROOT/'data'/'normalized'; DB=ROOT/'data'/'fantasy_tracker.sqlite'

def read(name):
 p=OUT/name
 if not p.exists(): return []
 with p.open('r',newline='',encoding='utf-8') as f: return list(csv.DictReader(f))

def write(name,rows):
 if not rows:return
 fs=[]; seen=set()
 for r in rows:
  for k in r:
   if k not in seen: seen.add(k); fs.append(k)
 with (OUT/name).open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=fs,extrasaction='ignore'); w.writeheader(); w.writerows(rows)

def replace(name,rows):
 con=sqlite3.connect(DB)
 try:
  con.execute(f'DROP TABLE IF EXISTS "{name}"')
  if rows:
   fs=list(rows[0]); con.execute(f'CREATE TABLE "{name}" ({", ".join(f"\"{c}\" TEXT" for c in fs)})'); qs=','.join('?' for _ in fs); cols=','.join(f'"{c}"' for c in fs)
   con.executemany(f'INSERT INTO "{name}" ({cols}) VALUES ({qs})',[[None if r.get(c) is None else str(r.get(c)) for c in fs] for r in rows])
  con.commit()
 finally: con.close()

def num(v,d=0.0):
 try:return float(v)
 except:return d

def main():
 scores=read('player_week_scores.csv'); rip={str(r.get('sleeper_id')):r for r in read('injury_ripple_players.csv')}; changed=0
 for r in scores:
  base=num(r.get('lineup_score')); x=rip.get(str(r.get('player_id'))); r['base_lineup_score_before_injury_ripple']=round(base,2); r['injury_ripple_points_applied']=0.0
  if not x: continue
  adj=max(-2.25,min(2.25,num(x.get('injury_ripple_points')))); r['injury_ripple_points_applied']=round(adj,2); r['injury_ripple_reasons']=x.get('reasons'); r['injury_ripple_confidence']=x.get('confidence')
  if adj:
   r['lineup_score']=round(max(0.0,base+adj),2); r['score_source']=str(r.get('score_source') or '')+' + injury ripple'; changed+=1
 write('player_week_scores.csv',scores); replace('player_week_scores',scores)
 print(json.dumps({'injury_ripple_adjusted_player_scores':changed,'cap':2.25},indent=2))
if __name__=='__main__': main()

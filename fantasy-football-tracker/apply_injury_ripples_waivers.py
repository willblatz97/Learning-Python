from __future__ import annotations
import csv, json
from pathlib import Path
ROOT=Path(__file__).resolve().parent; OUT=ROOT/'data'/'normalized'

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

def num(v,d=0.0):
 try:return float(v)
 except:return d

def main():
 rows=read('waiver_board.csv'); rip={str(r.get('sleeper_id')):r for r in read('injury_ripple_players.csv')}; changed=0
 for r in rows:
  x=rip.get(str(r.get('player_id') or r.get('sleeper_id') or ''))
  if not x: continue
  pts=num(x.get('injury_ripple_points')); bonus=max(-4.0,min(12.0,pts*4.0)); base=num(r.get('waiver_score'))
  r['injury_ripple_points']=round(pts,2); r['injury_ripple_waiver_bonus']=round(bonus,2); r['injury_ripple_reasons']=x.get('reasons')
  if bonus:
   r['waiver_score']=round(base+bonus,1); r['reasons']=(str(r.get('reasons') or '')+' | injury ripple '+str(x.get('reasons') or '')).strip(' |'); changed+=1
 rows.sort(key=lambda r:(str(r.get('league_id')), -num(r.get('waiver_score'))))
 write('waiver_board.csv',rows)
 print(json.dumps({'waiver_rows_adjusted_for_injury_ripples':changed,'max_bonus':12.0},indent=2))
if __name__=='__main__': main()

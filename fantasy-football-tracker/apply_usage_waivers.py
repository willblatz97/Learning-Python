from __future__ import annotations

import csv,json,math,sqlite3
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'data'/'normalized';DB=ROOT/'data'/'fantasy_tracker.sqlite'

def read(n):
 p=OUT/n;return list(csv.DictReader(p.open(encoding='utf-8'))) if p.exists() else []
def num(v,d=0.0):
 try:return float(v)
 except (TypeError,ValueError):return d
def write(n,rows):
 if not rows:return
 fs=[];seen=set()
 for r in rows:
  for k in r:
   if k not in seen:seen.add(k);fs.append(k)
 with (OUT/n).open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fs,extrasaction='ignore');w.writeheader();w.writerows(rows)
def sql(n,rows):
 con=sqlite3.connect(DB)
 try:
  con.execute(f'DROP TABLE IF EXISTS "{n}"')
  if rows:
   fs=list(rows[0]);defs=', '.join(f'"{c}" TEXT' for c in fs);cols=','.join(f'"{c}"' for c in fs);qs=','.join('?' for _ in fs)
   con.execute(f'CREATE TABLE "{n}" ({defs})');con.executemany(f'INSERT INTO "{n}" ({cols}) VALUES ({qs})',[[None if r.get(c) is None else str(r.get(c)) for c in fs] for r in rows])
  con.commit()
 finally:con.close()
def tier(s):
 return 'MUST ADD' if s>=85 else 'HIGH' if s>=74 else 'MEDIUM' if s>=62 else 'SPECULATIVE' if s>=50 else 'WATCH'
def band(s,kind):
 if s>=85:lo,hi=20,30
 elif s>=75:lo,hi=12,20
 elif s>=65:lo,hi=7,12
 elif s>=55:lo,hi=3,7
 elif s>=45:lo,hi=1,3
 else:lo,hi=0,1
 if kind=='chopped':lo=min(50,math.ceil(lo*1.4));hi=min(60,math.ceil(hi*1.5))
 return lo,hi

def main():
 rows=read('waiver_candidates.csv');usage={str(r.get('sleeper_id')):r for r in read('usage_trends.csv')};changed=0
 for r in rows:
  u=usage.get(str(r.get('player_id')))
  if not u:continue
  r['usage_season']=u.get('usage_season');r['usage_signal']=u.get('usage_signal');r['last3_snap_pct']=u.get('last3_offense_snap_pct');r['last3_opportunities_pg']=u.get('last3_opportunities_pg');r['usage_adjustment']=u.get('usage_adjustment')
  if str(u.get('current_season_data')).lower()!='true':continue
  adj=num(u.get('usage_adjustment'))
  if not adj:continue
  old=num(r.get('waiver_score'));new=round(max(0,min(100,old+adj*1.5)),1);r['waiver_score_before_usage']=old;r['waiver_score']=new;r['priority']=tier(new)
  kind='chopped' if 'chopped' in str(r.get('league') or '').lower() else 'dynasty' if 'dynasty' in str(r.get('league') or '').lower() else 'redraft';lo,hi=band(new,kind);remain=int(num(r.get('faab_remaining')))
  r['faab_low_pct']=lo;r['faab_high_pct']=hi;r['faab_low']=math.ceil(remain*lo/100);r['faab_high']=math.ceil(remain*hi/100)
  note=f"2026 usage {u.get('usage_signal')} — snaps L3 {u.get('last3_offense_snap_pct')}%, opps {u.get('last3_opportunities_pg')}/g"
  r['reasons']=(str(r.get('reasons') or '')+' | '+note).strip(' |');changed+=1
 grouped=defaultdict(list)
 for r in rows:grouped[str(r.get('league_id'))].append(r)
 final=[]
 for lid,rs in grouped.items():
  rs.sort(key=lambda x:(-num(x.get('waiver_score')),str(x.get('player') or '')))
  for i,r in enumerate(rs,1):r['league_rank']=i;final.append(r)
 write('waiver_candidates.csv',final);sql('waiver_candidates',final);print(json.dumps({'usage_adjusted_waiver_rows':changed},indent=2))
if __name__=='__main__':main()

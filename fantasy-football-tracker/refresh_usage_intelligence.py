from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'data'/'normalized'; DB=ROOT/'data'/'fantasy_tracker.sqlite'
ROSTER_URL='https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_2026.csv'
SNAP_URL=lambda season:f'https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.csv'
STAT_URL=lambda season:f'https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv'


def fetch(url):
    try:
        req=Request(url,headers={'User-Agent':'blatzzy-fantasy-tracker/1.0'})
        with urlopen(req,timeout=90) as r:return r.read().decode('utf-8-sig',errors='replace')
    except HTTPError as e:
        if e.code==404:return None
        raise

def parse(text):return list(csv.DictReader(io.StringIO(text))) if text else []
def read(name):
    p=OUT/name
    return list(csv.DictReader(p.open(encoding='utf-8'))) if p.exists() else []
def num(v,d=0.0):
    try:return float(v)
    except (TypeError,ValueError):return d
def integer(v,d=0):
    try:return int(float(v))
    except (TypeError,ValueError):return d
def pct(v):
    x=num(v,0.0)
    return x/100 if x>1 else x

def write(name,rows):
    if not rows:return
    fs=[];seen=set()
    for r in rows:
        for k in r:
            if k not in seen:seen.add(k);fs.append(k)
    with (OUT/name).open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fs,extrasaction='ignore');w.writeheader();w.writerows(rows)
def sql(name,rows):
    con=sqlite3.connect(DB)
    try:
        con.execute(f'DROP TABLE IF EXISTS "{name}"')
        if rows:
            fs=list(rows[0]);defs=', '.join(f'"{c}" TEXT' for c in fs);cols=','.join(f'"{c}"' for c in fs);qs=','.join('?' for _ in fs)
            con.execute(f'CREATE TABLE "{name}" ({defs})')
            con.executemany(f'INSERT INTO "{name}" ({cols}) VALUES ({qs})',[[None if r.get(c) is None else str(r.get(c)) for c in fs] for r in rows])
        con.commit()
    finally:con.close()

def avg(xs):return sum(xs)/len(xs) if xs else 0.0

def usage_signal(snap3,snap_season,opp3,opp_season):
    snap_delta=snap3-snap_season; opp_delta=opp3-opp_season
    if snap_delta>=.10 or opp_delta>=2.5:return 'RISING'
    if snap_delta<=-.10 or opp_delta<=-2.5:return 'FALLING'
    return 'STABLE'

def adjustment(current,pos,snap3,opp3,signal):
    if not current:return 0.0
    s=0.0
    if snap3>=.80:s+=2.0
    elif snap3>=.60:s+=1.0
    elif snap3<.25:s-=2.0
    if pos=='RB':
        if opp3>=15:s+=2.0
        elif opp3>=9:s+=1.0
        elif opp3<4:s-=1.0
    elif pos in {'WR','TE'}:
        if opp3>=7:s+=2.0
        elif opp3>=4:s+=1.0
        elif opp3<2:s-=1.0
    if signal=='RISING':s+=2.0
    elif signal=='FALLING':s-=2.0
    return max(-6.0,min(6.0,s))

def main():
    ts=datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    availability={str(r.get('player_id')):r for r in read('availability_matrix.csv')}
    roster=parse(fetch(ROSTER_URL))
    pfr_to_sleeper={str(r.get('pfr_id') or '').strip():str(r.get('sleeper_id') or '').strip() for r in roster if r.get('pfr_id') and r.get('sleeper_id')}
    gsis_to_sleeper={str(r.get('gsis_id') or '').strip():str(r.get('sleeper_id') or '').strip() for r in roster if r.get('gsis_id') and r.get('sleeper_id')}

    snap26=parse(fetch(SNAP_URL(2026))); stat26=parse(fetch(STAT_URL(2026)))
    current=bool([r for r in snap26 if str(r.get('game_type') or 'REG').upper()=='REG'])
    season=2026 if current else 2025
    snaps=snap26 if current else parse(fetch(SNAP_URL(2025)))
    stats=stat26 if current and stat26 else parse(fetch(STAT_URL(2025)))

    snap_by=defaultdict(list)
    for r in snaps:
        if str(r.get('game_type') or 'REG').upper()!='REG':continue
        sid=pfr_to_sleeper.get(str(r.get('pfr_player_id') or '').strip())
        if sid:snap_by[sid].append(r)
    stat_by=defaultdict(list)
    for r in stats:
        if str(r.get('season_type') or 'REG').upper()!='REG':continue
        sid=gsis_to_sleeper.get(str(r.get('player_id') or r.get('gsis_id') or '').strip())
        if sid:stat_by[sid].append(r)

    ids=set(snap_by)|set(stat_by)|set(availability)
    out=[]
    for sid in ids:
        av=availability.get(sid,{})
        pos=str(av.get('position') or '')
        if pos not in {'QB','RB','WR','TE'}:continue
        sr=sorted(snap_by.get(sid,[]),key=lambda r:integer(r.get('week')))
        tr=sorted(stat_by.get(sid,[]),key=lambda r:integer(r.get('week')))
        if not sr and not tr:continue
        snaps_all=[pct(r.get('offense_pct')) for r in sr]
        snaps3=snaps_all[-3:]
        latest_snap=snaps_all[-1] if snaps_all else 0.0
        latest_week=max([integer(r.get('week')) for r in sr+tr] or [0])

        def carries(r):return num(r.get('carries') or r.get('rushing_attempts'))
        def targets(r):return num(r.get('targets'))
        def receptions(r):return num(r.get('receptions'))
        if pos=='RB':opps=[carries(r)+targets(r) for r in tr]
        elif pos in {'WR','TE'}:opps=[targets(r) for r in tr]
        else:opps=[]
        targets_all=[targets(r) for r in tr]; carries_all=[carries(r) for r in tr]; rec_all=[receptions(r) for r in tr]
        opp3=avg(opps[-3:]); oppseason=avg(opps)
        snap3=avg(snaps3); snapseason=avg(snaps_all)
        signal=usage_signal(snap3,snapseason,opp3,oppseason)
        adj=adjustment(current,pos,snap3,opp3,signal)
        out.append({
            'snapshot_utc':ts,'sleeper_id':sid,'player':av.get('full_name'),'position':pos,'nfl_team':av.get('nfl_team'),
            'usage_season':season,'current_season_data':str(current),'games_with_snaps':len(sr),'games_with_stats':len(tr),'latest_week':latest_week,
            'latest_offense_snap_pct':round(latest_snap*100,1),'last3_offense_snap_pct':round(snap3*100,1),'season_offense_snap_pct':round(snapseason*100,1),
            'last_targets':targets_all[-1] if targets_all else 0,'last3_targets_pg':round(avg(targets_all[-3:]),2),'season_targets_pg':round(avg(targets_all),2),
            'last_carries':carries_all[-1] if carries_all else 0,'last3_carries_pg':round(avg(carries_all[-3:]),2),'season_carries_pg':round(avg(carries_all),2),
            'last_receptions':rec_all[-1] if rec_all else 0,'last3_receptions_pg':round(avg(rec_all[-3:]),2),
            'last3_opportunities_pg':round(opp3,2),'season_opportunities_pg':round(oppseason,2),'usage_signal':signal,'usage_adjustment':adj,
            'injury_status':av.get('injury_status'),'depth_chart_order':av.get('depth_chart_order'),'my_leagues':av.get('my_leagues'),'free_agent_leagues':av.get('free_agent_league_names'),
        })
    out.sort(key=lambda r:(-num(r.get('usage_adjustment')),-num(r.get('last3_offense_snap_pct')),-num(r.get('last3_opportunities_pg')),str(r.get('player') or '')))
    write('usage_trends.csv',out);sql('usage_trends',out)
    summary={'snapshot_utc':ts,'usage_season':season,'current_season_data':current,'snap_source':SNAP_URL(season),'stats_source':STAT_URL(season),'players':len(out),'latest_week':max([integer(r.get('latest_week')) for r in out] or [0]),'note':'2025 is context only until 2026 regular-season snap data exists; current-season usage adjustments automatically activate after games are played.'}
    (ROOT/'data'/'usage_summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True),encoding='utf-8')
    lines=['# Usage & Snap Intelligence','',summary['note'],'',f"Source season: {season}; latest week: {summary['latest_week']}",'','## Highest current usage signals']
    for r in out[:35]:
        lines.append(f"- {r['player']} ({r['position']} {r['nfl_team']}) — snaps L3 {r['last3_offense_snap_pct']}%; opps L3 {r['last3_opportunities_pg']}/g; {r['usage_signal']}; adjustment {r['usage_adjustment']:+g}")
    (ROOT/'data'/'usage_report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()

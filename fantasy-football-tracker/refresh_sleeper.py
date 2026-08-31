from __future__ import annotations

import csv
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

BASE = "https://api.sleeper.app/v1"
ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "normalized"
DB = ROOT / "data" / "fantasy_tracker.sqlite"


def get_json(path: str):
    req = Request(f"{BASE}{path}", headers={"User-Agent": "blatzzy-fantasy-tracker/1.0"})
    with urlopen(req, timeout=30) as r:
        return json.load(r)


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def flatten(prefix: str, obj: dict | None) -> dict:
    out = {}
    for k, v in (obj or {}).items():
        key = f"{prefix}_{k}"
        out[key] = json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v
    return out


def main():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    username = cfg["sleeper_username"]
    season = str(cfg["season"])
    league_ids = [str(x) for x in cfg["league_ids"]]
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    RAW.mkdir(parents=True, exist_ok=True); OUT.mkdir(parents=True, exist_ok=True)
    state = get_json("/state/nfl")
    user = get_json(f"/user/{username}")
    account_leagues = get_json(f"/user/{user['user_id']}/leagues/nfl/{season}")
    players = get_json("/players/nfl")
    save_json(RAW / "nfl_state.json", state); save_json(RAW / "user.json", user)
    save_json(RAW / "account_leagues.json", account_leagues); save_json(RAW / "players_nfl.json", players)
    current_week = int(state.get("week") or 1)

    league_rows=[]; owner_rows=[]; roster_rows=[]; ownership_rows=[]; pick_rows=[]; draft_rows=[]; draft_pick_rows=[]; matchup_rows=[]; transaction_rows=[]

    for league_id in league_ids:
        league=get_json(f"/league/{league_id}"); users=get_json(f"/league/{league_id}/users"); rosters=get_json(f"/league/{league_id}/rosters")
        traded=get_json(f"/league/{league_id}/traded_picks"); drafts=get_json(f"/league/{league_id}/drafts")
        league_dir=RAW/"leagues"/league_id
        for n,obj in [("league.json",league),("users.json",users),("rosters.json",rosters),("traded_picks.json",traded),("drafts.json",drafts)]: save_json(league_dir/n,obj)

        lrow={"snapshot_utc":ts,"league_id":league_id,"name":league.get("name"),"season":league.get("season"),"status":league.get("status"),"total_rosters":league.get("total_rosters"),"draft_id":league.get("draft_id"),"previous_league_id":league.get("previous_league_id"),"avatar":league.get("avatar"),"roster_positions":json.dumps(league.get("roster_positions") or [])}
        lrow.update(flatten("setting",league.get("settings"))); lrow.update(flatten("scoring",league.get("scoring_settings"))); league_rows.append(lrow)

        users_by_id={str(u.get("user_id")):u for u in users}
        for u in users:
            meta=u.get("metadata") or {}
            owner_rows.append({"snapshot_utc":ts,"league_id":league_id,"user_id":u.get("user_id"),"display_name":u.get("display_name"),"team_name":meta.get("team_name"),"is_owner":str(u.get("user_id"))==str(user.get("user_id")),"avatar":u.get("avatar")})
        for r in rosters:
            owner=users_by_id.get(str(r.get("owner_id")),{}); meta=r.get("metadata") or {}
            roster_rows.append({"snapshot_utc":ts,"league_id":league_id,"roster_id":r.get("roster_id"),"owner_id":r.get("owner_id"),"owner_display_name":owner.get("display_name"),"team_name":(owner.get("metadata") or {}).get("team_name"),"is_my_roster":str(r.get("owner_id"))==str(user.get("user_id")),"players_count":len(r.get("players") or []),"starters_count":len(r.get("starters") or []),"reserve":json.dumps(r.get("reserve") or []),"taxi":json.dumps(r.get("taxi") or []),"co_owners":json.dumps(r.get("co_owners") or []),"settings":json.dumps(r.get("settings") or {},sort_keys=True),"metadata":json.dumps(meta,sort_keys=True)})
            starters=set(r.get("starters") or []); reserve=set(r.get("reserve") or []); taxi=set(r.get("taxi") or [])
            for pid in r.get("players") or []:
                p=players.get(str(pid),{})
                ownership_rows.append({"snapshot_utc":ts,"league_id":league_id,"roster_id":r.get("roster_id"),"owner_id":r.get("owner_id"),"is_my_roster":str(r.get("owner_id"))==str(user.get("user_id")),"player_id":pid,"full_name":p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip(),"position":p.get("position"),"fantasy_positions":json.dumps(p.get("fantasy_positions") or []),"nfl_team":p.get("team"),"status":p.get("status"),"injury_status":p.get("injury_status"),"injury_body_part":p.get("injury_body_part"),"practice_participation":p.get("practice_participation"),"practice_description":p.get("practice_description"),"depth_chart_position":p.get("depth_chart_position"),"depth_chart_order":p.get("depth_chart_order"),"active":p.get("active"),"starter":pid in starters,"reserve":pid in reserve,"taxi":pid in taxi})

        for p in traded: pick_rows.append({"snapshot_utc":ts,"league_id":league_id,**p})
        for d in drafts:
            draft_id=str(d.get("draft_id")); draft_rows.append({"snapshot_utc":ts,"league_id":league_id,"draft_id":draft_id,"season":d.get("season"),"status":d.get("status"),"type":d.get("type"),"rounds":(d.get("settings") or {}).get("rounds"),"start_time":d.get("start_time"),"last_picked":d.get("last_picked"),"metadata":json.dumps(d.get("metadata") or {},sort_keys=True),"settings":json.dumps(d.get("settings") or {},sort_keys=True)})
            try:
                dpicks=get_json(f"/draft/{draft_id}/picks"); save_json(league_dir/f"draft_{draft_id}_picks.json",dpicks)
                for pk in dpicks:
                    pp=players.get(str(pk.get("player_id")),{})
                    draft_pick_rows.append({"snapshot_utc":ts,"league_id":league_id,"draft_id":draft_id,"pick_no":pk.get("pick_no"),"round":pk.get("round"),"draft_slot":pk.get("draft_slot"),"roster_id":pk.get("roster_id"),"picked_by":pk.get("picked_by"),"player_id":pk.get("player_id"),"full_name":pp.get("full_name"),"position":pp.get("position"),"nfl_team":pp.get("team"),"metadata":json.dumps(pk.get("metadata") or {},sort_keys=True)})
            except Exception as e: print(f"draft picks failed {draft_id}: {e}")

        # Pull the complete fantasy regular-season schedule so projected-finish simulations can use actual H2H opponents.
        settings=league.get("settings") or {}; playoff_start=int(settings.get("playoff_week_start") or 15)
        regular_end=max(current_week+1,playoff_start-1)
        for week in range(1,regular_end+1):
            try:
                matchups=get_json(f"/league/{league_id}/matchups/{week}"); save_json(league_dir/f"matchups_week_{week}.json",matchups)
                for m in matchups:
                    matchup_rows.append({"snapshot_utc":ts,"league_id":league_id,"week":week,"matchup_id":m.get("matchup_id"),"roster_id":m.get("roster_id"),"points":m.get("points"),"players":json.dumps(m.get("players") or []),"starters":json.dumps(m.get("starters") or []),"players_points":json.dumps(m.get("players_points") or {},sort_keys=True),"starters_points":json.dumps(m.get("starters_points") or [],sort_keys=True)})
            except Exception as e: print(f"matchups failed league={league_id} week={week}: {e}")

        for week in sorted(set([max(1,current_week-1),current_week])):
            try:
                txs=get_json(f"/league/{league_id}/transactions/{week}"); save_json(league_dir/f"transactions_week_{week}.json",txs)
                for t in txs:
                    transaction_rows.append({"snapshot_utc":ts,"league_id":league_id,"week":week,"transaction_id":t.get("transaction_id"),"type":t.get("type"),"status":t.get("status"),"created":t.get("created"),"status_updated":t.get("status_updated"),"roster_ids":json.dumps(t.get("roster_ids") or []),"adds":json.dumps(t.get("adds") or {},sort_keys=True),"drops":json.dumps(t.get("drops") or {},sort_keys=True),"draft_picks":json.dumps(t.get("draft_picks") or [],sort_keys=True),"waiver_budget":json.dumps(t.get("waiver_budget") or [],sort_keys=True),"settings":json.dumps(t.get("settings") or {},sort_keys=True)})
            except Exception as e: print(f"transactions failed league={league_id} week={week}: {e}")
        time.sleep(0.15)

    datasets={"leagues.csv":league_rows,"owners.csv":owner_rows,"rosters.csv":roster_rows,"ownership.csv":ownership_rows,"traded_picks.csv":pick_rows,"drafts.csv":draft_rows,"draft_picks.csv":draft_pick_rows,"matchups.csv":matchup_rows,"transactions.csv":transaction_rows}
    for name,rows in datasets.items():
        fields=[];seen=set()
        for row in rows:
            for k in row:
                if k not in seen: seen.add(k);fields.append(k)
        if fields: write_csv(OUT/name,rows,fields)

    DB.parent.mkdir(parents=True,exist_ok=True)
    if DB.exists(): DB.unlink()
    con=sqlite3.connect(DB)
    try:
        for name,rows in datasets.items():
            if not rows: continue
            table=name.removesuffix(".csv"); fields=[];seen=set()
            for row in rows:
                for k in row:
                    if k not in seen: seen.add(k);fields.append(k)
            con.execute(f'CREATE TABLE "{table}" ({", ".join([f"\"{c}\" TEXT" for c in fields])})')
            qs=','.join(['?']*len(fields)); col_sql=','.join([f'"{c}"' for c in fields])
            con.executemany(f'INSERT INTO "{table}" ({col_sql}) VALUES ({qs})',[[None if row.get(c) is None else str(row.get(c)) for c in fields] for row in rows])
        con.execute("CREATE INDEX idx_ownership_player ON ownership(player_id)"); con.execute("CREATE INDEX idx_ownership_league ON ownership(league_id)"); con.execute("CREATE INDEX idx_ownership_mine ON ownership(is_my_roster)"); con.commit()
    finally: con.close()

    my_rosters=[r for r in roster_rows if r["is_my_roster"]]
    summary={"snapshot_utc":ts,"sleeper_username":username,"sleeper_user_id":user.get("user_id"),"nfl_state":state,"configured_leagues":len(league_ids),"leagues_found":len(league_rows),"my_rosters_found":len(my_rosters),"owned_player_rows":sum(1 for r in ownership_rows if r["is_my_roster"]),"all_ownership_rows":len(ownership_rows),"transactions_rows":len(transaction_rows),"matchup_rows":len(matchup_rows),"league_names":{r["league_id"]:r["name"] for r in league_rows},"my_roster_ids":{r["league_id"]:r["roster_id"] for r in my_rosters}}
    save_json(ROOT/"data"/"summary.json",summary); print(json.dumps(summary,indent=2))

if __name__=="__main__": main()

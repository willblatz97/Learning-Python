from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
RAW = ROOT / "data" / "raw" / "external"
SUMMARY = ROOT / "data" / "summary.json"

PLAYERIDS_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"
WEEKLY_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/fp_latest_weekly.csv"
GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"


def fetch_text(url: str) -> str:
    req = Request(url, headers={"User-Agent": "blatzzy-fantasy-tracker/1.0"})
    with urlopen(req, timeout=60) as response:
        return response.read().decode("utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def clean_team(team: str | None) -> str:
    t = str(team or "").strip()
    aliases = {"JAC": "JAX", "LA": "LAR", "WSH": "WAS", "NEP": "NE", "GBP": "GB", "KCC": "KC", "SFO": "SF", "TBB": "TB", "NOS": "NO"}
    return aliases.get(t, t)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    nfl_state = summary.get("nfl_state") or {}
    season = int(nfl_state.get("season") or 2026)
    week = int(nfl_state.get("week") or 1)

    ids_text = fetch_text(PLAYERIDS_URL)
    weekly_text = fetch_text(WEEKLY_URL)
    games_text = fetch_text(GAMES_URL)
    (RAW / "fp_latest_weekly.csv").write_text(weekly_text, encoding="utf-8")
    (RAW / "games.csv").write_text(games_text, encoding="utf-8")

    fp_to_sleeper = {}
    for row in csv.DictReader(io.StringIO(ids_text)):
        fp = str(row.get("fantasypros_id") or "").strip()
        sid = str(row.get("sleeper_id") or "").strip()
        if fp and sid and fp != "NA" and sid != "NA":
            fp_to_sleeper[fp] = sid

    weekly_rows = []
    freshness_dates = set()
    for row in csv.DictReader(io.StringIO(weekly_text)):
        fp = str(row.get("fantasypros_id") or "").strip()
        sid = fp_to_sleeper.get(fp)
        if not sid:
            continue
        scrape = str(row.get("scrape_date") or "").strip()
        if scrape:
            freshness_dates.add(scrape)
        weekly_rows.append({
            "sleeper_id": sid,
            "fantasypros_id": fp,
            "player": row.get("player_name"),
            "position": row.get("pos"),
            "team": clean_team(row.get("team")),
            "rank": row.get("rank"),
            "ecr": row.get("ecr"),
            "sd": row.get("sd"),
            "best": row.get("best"),
            "worst": row.get("worst"),
            "pos_rank": row.get("pos_rank"),
            "start_sit_grade": row.get("start_sit_grade"),
            "projected_points": row.get("r2p_pts"),
            "opponent": clean_team(row.get("player_opponent")),
            "owned_avg": row.get("player_owned_avg"),
            "scrape_date": scrape,
        })
    weekly_fields = ["sleeper_id","fantasypros_id","player","position","team","rank","ecr","sd","best","worst","pos_rank","start_sit_grade","projected_points","opponent","owned_avg","scrape_date"]
    write_csv(OUT / "weekly_rankings.csv", weekly_rows, weekly_fields)

    schedule_rows = []
    team_rows = []
    for row in csv.DictReader(io.StringIO(games_text)):
        try:
            rseason = int(float(row.get("season") or 0))
            rweek = int(float(row.get("week") or 0))
        except ValueError:
            continue
        if rseason != season or rweek != week or str(row.get("game_type") or "REG") != "REG":
            continue
        home = clean_team(row.get("home_team"))
        away = clean_team(row.get("away_team"))
        schedule_rows.append({
            "game_id": row.get("game_id"), "season": rseason, "week": rweek,
            "gameday": row.get("gameday"), "weekday": row.get("weekday"), "gametime": row.get("gametime"),
            "home_team": home, "away_team": away,
            "spread_line": row.get("spread_line"), "total_line": row.get("total_line"),
            "roof": row.get("roof"), "surface": row.get("surface"), "temp": row.get("temp"), "wind": row.get("wind"),
            "stadium": row.get("stadium"), "location": row.get("location"),
        })
        for team, opp, side in [(home, away, "home"), (away, home, "away")]:
            team_rows.append({
                "season": rseason, "week": rweek, "team": team, "opponent": opp, "home_away": side,
                "gameday": row.get("gameday"), "weekday": row.get("weekday"), "gametime": row.get("gametime"),
                "spread_line": row.get("spread_line"), "total_line": row.get("total_line"),
                "roof": row.get("roof"), "surface": row.get("surface"), "temp": row.get("temp"), "wind": row.get("wind"),
                "stadium": row.get("stadium"),
            })
    game_fields = ["game_id","season","week","gameday","weekday","gametime","home_team","away_team","spread_line","total_line","roof","surface","temp","wind","stadium","location"]
    team_fields = ["season","week","team","opponent","home_away","gameday","weekday","gametime","spread_line","total_line","roof","surface","temp","wind","stadium"]
    write_csv(OUT / "week_games.csv", schedule_rows, game_fields)
    write_csv(OUT / "team_week_context.csv", team_rows, team_fields)

    current_weekly = False
    for d in freshness_dates:
        try:
            if date.fromisoformat(d).year == season:
                current_weekly = True
        except ValueError:
            pass
    result = {
        "season": season,
        "week": week,
        "weekly_ranking_rows": len(weekly_rows),
        "weekly_ranking_scrape_dates": sorted(freshness_dates),
        "weekly_rankings_current_season": current_weekly,
        "games_this_week": len(schedule_rows),
        "team_context_rows": len(team_rows),
        "schedule_source": "nflverse/nfldata games.csv",
        "weekly_source": "DynastyProcess FantasyPros weekly consensus mirror",
    }
    (ROOT / "data" / "weekly_context_summary.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

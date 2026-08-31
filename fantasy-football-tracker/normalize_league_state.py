from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
DB = ROOT / "data" / "fantasy_tracker.sqlite"
EXPOSURE_SUMMARY = ROOT / "data" / "exposure_summary.json"
CONFIG = ROOT / "config.json"


def read_csv(name: str) -> list[dict]:
    with (OUT / name).open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(name: str, rows: list[dict]):
    if not rows:
        return
    with (OUT / name).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def replace_sqlite_table(table: str, rows: list[dict]):
    if not rows:
        return
    con = sqlite3.connect(DB)
    try:
        con.execute(f'DROP TABLE IF EXISTS "{table}"')
        fields = list(rows[0].keys())
        cols = ", ".join(f'"{c}" TEXT' for c in fields)
        con.execute(f'CREATE TABLE "{table}" ({cols})')
        qs = ",".join("?" for _ in fields)
        col_sql = ",".join(f'"{c}"' for c in fields)
        con.executemany(
            f'INSERT INTO "{table}" ({col_sql}) VALUES ({qs})',
            [[None if row.get(c) is None else str(row.get(c)) for c in fields] for row in rows],
        )
        con.commit()
    finally:
        con.close()


def main():
    leagues = read_csv("leagues.csv")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    labels = cfg.get("league_labels", {})
    predraft = {str(r["league_id"]): r for r in leagues if str(r.get("status")) == "pre_draft"}
    if not predraft:
        return

    exposure = read_csv("my_exposure.csv")
    for row in exposure:
        free_names = [x.strip() for x in str(row.get("free_agent_league_names") or "").split("|") if x.strip()]
        for lid in predraft:
            col = f"league_{lid}"
            if row.get(col) == "FA":
                row[col] = "PREDRAFT"
                label = labels.get(lid, predraft[lid].get("name", lid))
                free_names = [x for x in free_names if x != label]
        row["free_agent_league_names"] = " | ".join(free_names)
    write_csv("my_exposure.csv", exposure)
    replace_sqlite_table("my_exposure", exposure)

    availability = read_csv("availability_matrix.csv")
    for row in availability:
        for lid in predraft:
            col = f"league_{lid}"
            if row.get(col) == "FA":
                row[col] = "PREDRAFT"
        fa_count = sum(1 for lid in cfg["league_ids"] if row.get(f"league_{lid}") == "FA")
        row["free_agent_leagues"] = fa_count
        row["multi_league_free_agent"] = str(fa_count >= 2)
        rostered = int(row.get("rostered_leagues") or 0)
        row["rostered_somewhere_free_somewhere"] = str(rostered > 0 and fa_count > 0)
    write_csv("availability_matrix.csv", availability)
    replace_sqlite_table("availability_matrix", availability)

    summary = json.loads(EXPOSURE_SUMMARY.read_text(encoding="utf-8"))
    market = [
        r for r in availability
        if r.get("rostered_somewhere_free_somewhere") == "True" and int(r.get("free_agent_leagues") or 0) >= 1
    ]
    market.sort(key=lambda r: (-int(r.get("rostered_leagues") or 0), -int(r.get("free_agent_leagues") or 0), str(r.get("full_name") or "")))
    summary["pre_draft_league_ids"] = sorted(predraft)
    summary["market_signal_players"] = [
        {k: r.get(k) for k in ["player_id", "full_name", "position", "nfl_team", "rostered_leagues", "free_agent_leagues", "my_leagues"]}
        for r in market[:50]
    ]
    EXPOSURE_SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps({
        "pre_draft_leagues": {lid: predraft[lid].get("name") for lid in predraft},
        "market_signal_players_after_filter": len(summary["market_signal_players"]),
    }, indent=2))


if __name__ == "__main__":
    main()

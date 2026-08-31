from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
RAW = ROOT / "data" / "raw" / "external"

PLAYERIDS_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"
ECR_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_fpecr_latest.csv"


def fetch_text(url: str) -> str:
    req = Request(url, headers={"User-Agent": "blatzzy-fantasy-tracker/1.0"})
    with urlopen(req, timeout=45) as response:
        return response.read().decode("utf-8")


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    ids_text = fetch_text(PLAYERIDS_URL)
    ecr_text = fetch_text(ECR_URL)
    (RAW / "db_playerids.csv").write_text(ids_text, encoding="utf-8")
    (RAW / "db_fpecr_latest.csv").write_text(ecr_text, encoding="utf-8")

    fp_to_sleeper = {}
    for row in csv.DictReader(io.StringIO(ids_text)):
        fp = str(row.get("fantasypros_id") or "").strip()
        sleeper = str(row.get("sleeper_id") or "").strip()
        if fp and sleeper and fp != "NA" and sleeper != "NA":
            fp_to_sleeper[fp] = sleeper

    wanted_types = {"ro", "dsf", "do"}
    rows = []
    for row in csv.DictReader(io.StringIO(ecr_text)):
        ecr_type = str(row.get("ecr_type") or "")
        if ecr_type not in wanted_types:
            continue
        fp = str(row.get("id") or "").strip()
        sleeper = fp_to_sleeper.get(fp)
        if not sleeper:
            continue
        rows.append({
            "sleeper_id": sleeper,
            "fantasypros_id": fp,
            "player": row.get("player"),
            "position": row.get("pos"),
            "team": row.get("team"),
            "ecr_type": ecr_type,
            "ecr": row.get("ecr"),
            "sd": row.get("sd"),
            "best": row.get("best"),
            "worst": row.get("worst"),
            "player_owned_avg": row.get("player_owned_avg"),
            "scrape_date": row.get("scrape_date"),
        })

    rows.sort(key=lambda r: (r["ecr_type"], float(r["ecr"]) if str(r.get("ecr") or "").replace(".", "", 1).isdigit() else 99999, r["player"] or ""))
    write_csv(OUT / "external_rankings.csv", rows)

    summary = {
        "source": "dynastyprocess/data (FantasyPros consensus mirror)",
        "player_id_mappings": len(fp_to_sleeper),
        "ranking_rows": len(rows),
        "scrape_dates": sorted({r["scrape_date"] for r in rows if r.get("scrape_date")}),
        "ranking_types": sorted({r["ecr_type"] for r in rows}),
    }
    (ROOT / "data" / "external_rankings_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

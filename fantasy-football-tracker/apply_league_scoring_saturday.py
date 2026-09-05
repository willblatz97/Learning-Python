from __future__ import annotations

import csv
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
RAW = ROOT / "data" / "raw" / "external"
SUMMARY = ROOT / "data" / "summary.json"

RETURN_STATS = {
    "kr_yd",
    "pr_yd",
    "st_td",
    "st_ff",
    "st_fum_rec",
    "st_tkl_solo",
}
POSITION_REC_BONUSES = {
    "QB": "bonus_rec_qb",
    "RB": "bonus_rec_rb",
    "WR": "bonus_rec_wr",
    "TE": "bonus_rec_te",
}


def read_csv(name: str) -> list[dict]:
    path = OUT / name
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(name: str, rows: list[dict]) -> None:
    if not rows:
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with (OUT / name).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def fnum(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fetch_json(url: str):
    request = Request(url, headers={"User-Agent": "blatzzy-fantasy-tracker/1.0"})
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_sleeper_projections(season: int, week: int) -> tuple[list[dict], str]:
    params = [
        ("season_type", "regular"),
        ("position[]", "QB"),
        ("position[]", "RB"),
        ("position[]", "WR"),
        ("position[]", "TE"),
        ("position[]", "FLEX"),
        ("position[]", "K"),
        ("position[]", "DEF"),
    ]
    query = urlencode(params)
    urls = [
        f"https://api.sleeper.com/projections/nfl/{season}/{week}?{query}",
        f"https://api.sleeper.app/projections/nfl/{season}/{week}?{query}",
    ]
    last_error = None
    for url in urls:
        try:
            data = fetch_json(url)
            if isinstance(data, list) and data:
                return data, url
            last_error = RuntimeError(f"projection endpoint returned {type(data).__name__} with no rows")
        except Exception as exc:  # undocumented endpoint: fail soft and keep prior model
            last_error = exc
    raise RuntimeError(f"Sleeper weekly projections unavailable: {last_error}")


def league_scoring_settings(league_row: dict) -> dict[str, float]:
    scoring: dict[str, float] = {}
    for key, value in league_row.items():
        if not key.startswith("scoring_"):
            continue
        points = fnum(value, None)
        if points is None or points == 0:
            continue
        scoring[key.removeprefix("scoring_")] = points
    return scoring


def score_projection(stats: dict, scoring: dict[str, float], position: str) -> tuple[float, int]:
    total = 0.0
    matched = 0
    for stat, points_per_unit in scoring.items():
        if stat in RETURN_STATS or stat.startswith("idp_"):
            continue
        value = fnum(stats.get(stat), None)
        if value is None:
            continue
        total += value * points_per_unit
        matched += 1

    # Sleeper league settings express positional reception premiums as bonus_rec_*.
    # Some projection feeds omit those derived stat keys even when receptions exist.
    bonus_key = POSITION_REC_BONUSES.get(position)
    if bonus_key and bonus_key in scoring and bonus_key not in stats:
        receptions = fnum(stats.get("rec"), None)
        if receptions is not None:
            total += receptions * scoring[bonus_key]
            matched += 1

    return round(total, 2), matched


def projection_player_id(row: dict) -> str:
    player = row.get("player") if isinstance(row.get("player"), dict) else {}
    return str(row.get("player_id") or player.get("player_id") or "")


def projection_position(row: dict) -> str:
    player = row.get("player") if isinstance(row.get("player"), dict) else {}
    fantasy_positions = player.get("fantasy_positions")
    if isinstance(fantasy_positions, list) and fantasy_positions:
        default_pos = fantasy_positions[0]
    else:
        default_pos = ""
    return str(player.get("position") or default_pos or row.get("category") or "")


def main() -> None:
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    nfl_state = summary.get("nfl_state") or {}
    season = int(nfl_state.get("season") or 2026)
    week = int(nfl_state.get("week") or 1)

    scores = read_csv("player_week_scores.csv")
    leagues = {str(row.get("league_id")): row for row in read_csv("leagues.csv")}
    weekly = {str(row.get("sleeper_id")): row for row in read_csv("weekly_rankings.csv")}

    RAW.mkdir(parents=True, exist_ok=True)
    try:
        projections, source_url = fetch_sleeper_projections(season, week)
    except Exception as exc:
        result = {
            "season": season,
            "week": week,
            "status": "skipped",
            "reason": str(exc),
            "rows_updated": 0,
        }
        (ROOT / "data" / "sleeper_projection_summary.json").write_text(
            json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
        return

    (RAW / "sleeper_weekly_projections.json").write_text(
        json.dumps(projections, indent=2, sort_keys=True), encoding="utf-8"
    )
    projection_lookup = {
        projection_player_id(row): row
        for row in projections
        if projection_player_id(row)
    }

    normalized = []
    for row in projections:
        pid = projection_player_id(row)
        if not pid:
            continue
        player = row.get("player") if isinstance(row.get("player"), dict) else {}
        stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
        normalized.append(
            {
                "sleeper_id": pid,
                "player": player.get("full_name") or player.get("name"),
                "position": projection_position(row),
                "team": row.get("team") or player.get("team"),
                "opponent": row.get("opponent"),
                "pts_ppr": stats.get("pts_ppr"),
                "pts_half_ppr": stats.get("pts_half_ppr"),
                "pts_std": stats.get("pts_std"),
                "stats_json": json.dumps(stats, sort_keys=True),
            }
        )
    write_csv("sleeper_weekly_projections.csv", normalized)

    updated = 0
    skipped_no_projection = 0
    skipped_no_statline = 0
    league_counts: dict[str, int] = {}

    for row in scores:
        pid = str(row.get("player_id") or "")
        lid = str(row.get("league_id") or "")
        projection = projection_lookup.get(pid)
        league = leagues.get(lid)
        if not projection or not league:
            skipped_no_projection += 1
            continue

        stats = projection.get("stats") if isinstance(projection.get("stats"), dict) else {}
        # ADP-only placeholder rows are not weekly point projections.
        if all(stats.get(key) is None for key in ("pts_ppr", "pts_half_ppr", "pts_std")):
            skipped_no_statline += 1
            continue

        position = str(row.get("position") or projection_position(projection))
        scoring = league_scoring_settings(league)
        league_points, matched = score_projection(stats, scoring, position)
        if matched == 0:
            skipped_no_statline += 1
            continue

        old_score = fnum(row.get("lineup_score"), 0.0)
        generic = fnum((weekly.get(pid) or {}).get("projected_points"), None)
        context_adjustment = 0.0 if generic is None else old_score - generic

        # Questionable tags are often camp/preseason carryovers before official
        # weekly injury reports. Keep the monitor flag, but do not automatically
        # dock an injury-aware vendor projection by another 2.5 points.
        injury_note = str(row.get("injury_note") or "")
        practice = str(row.get("practice_participation") or "").strip()
        q_penalty_restored = 0.0
        if generic is not None and "QUESTIONABLE" in injury_note and not practice:
            context_adjustment += 2.5
            q_penalty_restored = 2.5

        new_score = round(max(0.0, league_points + context_adjustment), 2)
        row["base_lineup_score_before_league_scoring"] = round(old_score, 2)
        row["generic_weekly_projection"] = generic
        row["sleeper_league_projection_nonreturn"] = league_points
        row["league_scoring_context_adjustment"] = round(context_adjustment, 2)
        row["questionable_penalty_restored"] = q_penalty_restored
        row["league_projection_matched_stats"] = matched
        row["lineup_score"] = new_score
        row["score_source"] = "Sleeper raw-stat projection × league scoring"
        updated += 1
        league_counts[lid] = league_counts.get(lid, 0) + 1

    write_csv("player_week_scores.csv", scores)
    result = {
        "season": season,
        "week": week,
        "status": "ok",
        "source_url": source_url,
        "projection_rows": len(projections),
        "rows_updated": updated,
        "skipped_no_projection": skipped_no_projection,
        "skipped_no_statline": skipped_no_statline,
        "league_rows_updated": league_counts,
        "return_stats_excluded_for_return_role_layer": sorted(RETURN_STATS),
        "idp_stats_excluded_for_idp_layer": True,
    }
    (ROOT / "data" / "sleeper_projection_summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

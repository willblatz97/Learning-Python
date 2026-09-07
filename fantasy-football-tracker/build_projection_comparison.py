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

IDP_POSITIONS = {"DL", "DE", "DT", "LB", "DB", "CB", "S"}
PROJECTION_POSITIONS = [
    "QB", "RB", "WR", "TE", "FLEX", "K", "DEF",
    "DL", "DE", "DT", "LB", "DB", "CB", "S",
]
POSITION_REC_BONUSES = {
    "QB": "bonus_rec_qb",
    "RB": "bonus_rec_rb",
    "WR": "bonus_rec_wr",
    "TE": "bonus_rec_te",
}
IDP_ALIASES = {
    "idp_tkl": ("idp_tkl", "tkl"),
    "idp_tkl_solo": ("idp_tkl_solo", "tkl_solo"),
    "idp_tkl_ast": ("idp_tkl_ast", "tkl_ast"),
    "idp_tkl_loss": ("idp_tkl_loss", "tkl_loss"),
    "idp_ff": ("idp_ff", "ff"),
    "idp_fum_rec": ("idp_fum_rec", "fum_rec"),
    "idp_sack": ("idp_sack", "sack"),
    "idp_qb_hit": ("idp_qb_hit", "qb_hit"),
    "idp_int": ("idp_int", "int"),
    "idp_pass_def": ("idp_pass_def", "pass_def"),
    "idp_def_td": ("idp_def_td", "def_td"),
    "idp_safe": ("idp_safe", "safe"),
    "idp_blk_kick": ("idp_blk_kick", "blk_kick"),
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


def fetch_sleeper_projections(season: int, week: int) -> tuple[list[dict], str]:
    params = [("season_type", "regular")]
    params.extend(("position[]", pos) for pos in PROJECTION_POSITIONS)
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
        except Exception as exc:
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


def first_stat(stats: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in stats and stats.get(key) is not None:
            return fnum(stats.get(key), None)
    return None


def sleeper_custom_score(stats: dict, scoring: dict[str, float], position: str) -> tuple[float | None, int]:
    if not stats:
        return None, 0

    is_idp = position.upper() in IDP_POSITIONS
    total = 0.0
    matched = 0

    if is_idp:
        for scoring_key, aliases in IDP_ALIASES.items():
            points_per_unit = scoring.get(scoring_key)
            if points_per_unit is None:
                continue
            value = first_stat(stats, aliases)
            if value is None:
                continue
            total += value * points_per_unit
            matched += 1

        for stat in ("st_td", "st_ff", "st_fum_rec", "st_tkl_solo"):
            points_per_unit = scoring.get(stat)
            value = fnum(stats.get(stat), None)
            if points_per_unit is None or value is None:
                continue
            total += value * points_per_unit
            matched += 1
    else:
        for stat, points_per_unit in scoring.items():
            if stat.startswith("idp_"):
                continue
            value = fnum(stats.get(stat), None)
            if value is None:
                continue
            total += value * points_per_unit
            matched += 1

        bonus_key = POSITION_REC_BONUSES.get(position.upper())
        if bonus_key and bonus_key in scoring and bonus_key not in stats:
            receptions = fnum(stats.get("rec"), None)
            if receptions is not None:
                total += receptions * scoring[bonus_key]
                matched += 1

    if matched == 0:
        return None, 0
    return round(total, 2), matched


def load_starters(week: int) -> dict[tuple[str, str], list[str]]:
    starters: dict[tuple[str, str], list[str]] = {}
    league_root = ROOT / "data" / "raw" / "leagues"
    if not league_root.exists():
        return starters
    for league_dir in league_root.iterdir():
        if not league_dir.is_dir():
            continue
        path = league_dir / f"matchups_week_{week}.json"
        if not path.exists():
            continue
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            roster_id = str(row.get("roster_id") or "")
            lineup = row.get("starters") or []
            if roster_id and isinstance(lineup, list):
                starters[(league_dir.name, roster_id)] = [str(pid) for pid in lineup if pid]
    return starters


def main() -> None:
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    nfl_state = summary.get("nfl_state") or {}
    season = int(nfl_state.get("season") or 2026)
    week = int(nfl_state.get("week") or 1)

    scores = read_csv("player_week_scores.csv")
    leagues = {str(row.get("league_id")): row for row in read_csv("leagues.csv")}
    rosters = read_csv("rosters.csv")
    roster_meta = {
        (str(row.get("league_id")), str(row.get("roster_id"))): row
        for row in rosters
    }
    starters = load_starters(week)

    try:
        projections, source_url = fetch_sleeper_projections(season, week)
        RAW.mkdir(parents=True, exist_ok=True)
        (RAW / "sleeper_weekly_projections_full_comparison.json").write_text(
            json.dumps(projections, indent=2, sort_keys=True), encoding="utf-8"
        )
    except Exception as exc:
        fallback = RAW / "sleeper_weekly_projections_full_comparison.json"
        if fallback.exists():
            projections = json.loads(fallback.read_text(encoding="utf-8"))
            source_url = f"fallback:{fallback.name}"
        else:
            result = {
                "season": season,
                "week": week,
                "status": "skipped",
                "reason": str(exc),
            }
            (ROOT / "data" / "projection_comparison.json").write_text(
                json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
            )
            print(json.dumps(result, indent=2))
            return

    projection_lookup = {
        projection_player_id(row): row
        for row in projections
        if projection_player_id(row)
    }
    score_lookup = {
        (str(row.get("league_id")), str(row.get("roster_id")), str(row.get("player_id"))): row
        for row in scores
    }

    player_rows: list[dict] = []
    team_rows: list[dict] = []

    for (league_id, roster_id), lineup in sorted(starters.items()):
        league = leagues.get(league_id)
        if not league:
            continue
        scoring = league_scoring_settings(league)
        meta = roster_meta.get((league_id, roster_id), {})
        our_total = 0.0
        sleeper_total = 0.0
        covered = 0
        total_starters = len(lineup)

        layer_totals = {
            "league_context": 0.0,
            "idp": 0.0,
            "usage": 0.0,
            "ol": 0.0,
            "weather": 0.0,
            "injury_ripple": 0.0,
            "return": 0.0,
        }

        for pid in lineup:
            score_row = score_lookup.get((league_id, roster_id, pid), {})
            projection = projection_lookup.get(pid)
            position = str(score_row.get("position") or (projection_position(projection) if projection else ""))
            stats = projection.get("stats") if projection and isinstance(projection.get("stats"), dict) else {}
            sleeper_points, matched = sleeper_custom_score(stats, scoring, position)

            our_points = fnum(score_row.get("lineup_score"), 0.0) or 0.0
            our_total += our_points
            if sleeper_points is not None:
                sleeper_total += sleeper_points
                covered += 1

            layers = {
                "league_context": fnum(score_row.get("league_scoring_context_adjustment"), 0.0) or 0.0,
                "idp": fnum(score_row.get("idp_points_applied"), 0.0) or 0.0,
                "usage": fnum(score_row.get("usage_points_applied"), 0.0) or 0.0,
                "ol": fnum(score_row.get("ol_points_applied"), 0.0) or 0.0,
                "weather": fnum(score_row.get("weather_points_applied"), 0.0) or 0.0,
                "injury_ripple": fnum(score_row.get("injury_ripple_points_applied"), 0.0) or 0.0,
                "return": fnum(score_row.get("return_points_applied"), 0.0) or 0.0,
            }
            for key, value in layers.items():
                layer_totals[key] += value

            player_rows.append({
                "season": season,
                "week": week,
                "league_id": league_id,
                "league": league.get("name") or league.get("league") or "",
                "roster_id": roster_id,
                "team": meta.get("team_name") or meta.get("owner_display_name") or "",
                "is_my_roster": meta.get("is_my_roster"),
                "player_id": pid,
                "player": score_row.get("player") or "",
                "position": position,
                "our_projection": round(our_points, 2),
                "sleeper_projection": sleeper_points,
                "delta_our_minus_sleeper": (
                    round(our_points - sleeper_points, 2) if sleeper_points is not None else None
                ),
                "sleeper_stats_matched": matched,
                "league_context_points": round(layers["league_context"], 2),
                "idp_points": round(layers["idp"], 2),
                "usage_points": round(layers["usage"], 2),
                "ol_points": round(layers["ol"], 2),
                "weather_points": round(layers["weather"], 2),
                "injury_ripple_points": round(layers["injury_ripple"], 2),
                "return_points": round(layers["return"], 2),
                "injury_status": score_row.get("injury_status") or "",
                "score_source": score_row.get("score_source") or "",
            })

        coverage_pct = round(100.0 * covered / total_starters, 1) if total_starters else 0.0
        team_rows.append({
            "season": season,
            "week": week,
            "league_id": league_id,
            "league": league.get("name") or league.get("league") or "",
            "roster_id": roster_id,
            "team": meta.get("team_name") or meta.get("owner_display_name") or "",
            "is_my_roster": meta.get("is_my_roster"),
            "our_projection": round(our_total, 2),
            "sleeper_projection": round(sleeper_total, 2),
            "delta_our_minus_sleeper": (
                round(our_total - sleeper_total, 2) if covered == total_starters else None
            ),
            "sleeper_covered_starters": covered,
            "total_starters": total_starters,
            "sleeper_coverage_pct": coverage_pct,
            "sleeper_projection_is_partial": covered != total_starters,
            "league_context_points": round(layer_totals["league_context"], 2),
            "idp_points": round(layer_totals["idp"], 2),
            "usage_points": round(layer_totals["usage"], 2),
            "ol_points": round(layer_totals["ol"], 2),
            "weather_points": round(layer_totals["weather"], 2),
            "injury_ripple_points": round(layer_totals["injury_ripple"], 2),
            "return_points": round(layer_totals["return"], 2),
        })

    write_csv("projection_comparison_players.csv", player_rows)
    write_csv("projection_comparison_teams.csv", team_rows)

    my_teams = [row for row in team_rows if str(row.get("is_my_roster")).lower() == "true"]
    payload = {
        "season": season,
        "week": week,
        "status": "ok",
        "source_url": source_url,
        "projection_rows": len(projections),
        "my_teams": my_teams,
        "all_teams": team_rows,
    }
    (ROOT / "data" / "projection_comparison.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    lines = [
        f"# Projection Comparison — Week {week}",
        "",
        "Our model = Sleeper raw-stat baseline re-scored through the exact league rules, then adjusted by the War Room layers.",
        "Sleeper = Sleeper raw projected stat lines scored through the same league settings for an apples-to-apples baseline.",
        "",
        "| League | Team | Our model | Sleeper | Delta | Coverage |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in my_teams:
        sleeper_label = f"{row['sleeper_projection']:.2f}"
        if row["sleeper_projection_is_partial"]:
            sleeper_label += "*"
        delta = row["delta_our_minus_sleeper"]
        delta_label = "—" if delta is None else f"{delta:+.2f}"
        lines.append(
            f"| {row['league']} | {row['team']} | {row['our_projection']:.2f} | "
            f"{sleeper_label} | {delta_label} | {row['sleeper_coverage_pct']:.1f}% |"
        )
    lines.extend([
        "",
        "*Partial means Sleeper did not provide a usable weekly stat line for every starter; the total is not a full team comparison.",
        "",
        "## War Room adjustment layers on user lineups",
        "",
        "| League | League context | IDP | Usage | OL | Weather | Injury ripple | Returns |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in my_teams:
        lines.append(
            f"| {row['league']} | {row['league_context_points']:+.2f} | "
            f"{row['idp_points']:+.2f} | {row['usage_points']:+.2f} | "
            f"{row['ol_points']:+.2f} | {row['weather_points']:+.2f} | "
            f"{row['injury_ripple_points']:+.2f} | {row['return_points']:+.2f} |"
        )

    (ROOT / "data" / "projection_comparison.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": "ok",
        "season": season,
        "week": week,
        "projection_rows": len(projections),
        "teams_compared": len(team_rows),
        "my_teams": my_teams,
    }, indent=2))


if __name__ == "__main__":
    main()

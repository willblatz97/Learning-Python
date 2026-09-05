from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
CONFIG = ROOT / "config.json"
SUMMARY = ROOT / "data" / "saturday_summary.json"


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


def truthy(value) -> bool:
    return str(value).lower() in {"true", "1", "yes"}


def num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def is_chopped(label: str) -> bool:
    s = str(label or "").lower()
    return "chopped" in s or "surviving the chamber" in s


def main() -> None:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    report = json.loads(SUMMARY.read_text(encoding="utf-8"))
    labels = cfg.get("league_labels", {})
    week = str(report.get("week") or "")

    leagues = {str(r.get("league_id")): r for r in read_csv("leagues.csv")}
    rosters = read_csv("rosters.csv")
    matchups = [r for r in read_csv("matchups.csv") if str(r.get("week")) == week]
    scores = read_csv("player_week_scores.csv")
    matchup_summary = read_csv("matchup_summary.csv")

    roster_lookup = {(str(r.get("league_id")), str(r.get("roster_id"))): r for r in rosters}
    matchup_lookup = {(str(r.get("league_id")), str(r.get("roster_id"))): r for r in matchups}
    score_lookup = {
        (str(r.get("league_id")), str(r.get("roster_id")), str(r.get("player_id"))): r
        for r in scores
    }

    reconciliation: dict[str, dict] = {}

    for lid, league_row in leagues.items():
        label = labels.get(lid, league_row.get("name", lid))
        if not is_chopped(label):
            continue

        mine = next(
            (r for r in rosters if str(r.get("league_id")) == lid and truthy(r.get("is_my_roster"))),
            None,
        )
        if not mine:
            continue
        my_rid = str(mine.get("roster_id"))

        roster_views: list[dict] = []
        for rr in [r for r in rosters if str(r.get("league_id")) == lid]:
            rid = str(rr.get("roster_id"))
            matchup = matchup_lookup.get((lid, rid), {})
            try:
                starters = [str(x) for x in json.loads(matchup.get("starters") or "[]")]
            except json.JSONDecodeError:
                starters = []

            expected = 0.0
            nonreturn = 0.0
            return_added = 0.0
            player_rows: list[dict] = []
            for pid in starters:
                row = score_lookup.get((lid, rid, pid), {})
                final_score = num(row.get("lineup_score"))
                before_return = num(row.get("base_lineup_score_before_return"), final_score)
                applied_return = num(row.get("return_points_applied"), max(0.0, final_score - before_return))
                expected += final_score
                nonreturn += before_return
                return_added += applied_return
                player_rows.append(
                    {
                        "player_id": pid,
                        "player": row.get("player"),
                        "position": row.get("position"),
                        "nonreturn_projection": round(before_return, 2),
                        "return_points_applied": round(applied_return, 2),
                        "return_roles": row.get("return_roles"),
                        "return_confidence": row.get("return_confidence"),
                        "return_adjusted_projection": round(final_score, 2),
                    }
                )

            roster_views.append(
                {
                    "roster_id": rid,
                    "team": rr.get("team_name") or rr.get("owner_display_name"),
                    "is_my_roster": rid == my_rid,
                    "nonreturn_projection": round(nonreturn, 2),
                    "projected_return_points_added": round(return_added, 2),
                    "return_adjusted_projection": round(expected, 2),
                    "starters": player_rows,
                }
            )

        expected_order = sorted(roster_views, key=lambda r: r["return_adjusted_projection"], reverse=True)
        nonreturn_order = sorted(roster_views, key=lambda r: r["nonreturn_projection"], reverse=True)
        my_view = next((r for r in roster_views if r["is_my_roster"]), None)
        if not my_view:
            continue

        expected_others = [r["return_adjusted_projection"] for r in roster_views if not r["is_my_roster"]]
        nonreturn_others = [r["nonreturn_projection"] for r in roster_views if not r["is_my_roster"]]
        expected_line = min(expected_others) if expected_others else 0.0
        nonreturn_line = min(nonreturn_others) if nonreturn_others else 0.0
        expected_rank = next((i + 1 for i, r in enumerate(expected_order) if r["is_my_roster"]), None)
        nonreturn_rank = next((i + 1 for i, r in enumerate(nonreturn_order) if r["is_my_roster"]), None)

        payload = {
            "league_id": lid,
            "league": label,
            "team": my_view["team"],
            "return_adjusted_projection": my_view["return_adjusted_projection"],
            "projected_return_points_added": my_view["projected_return_points_added"],
            "nonreturn_projection": my_view["nonreturn_projection"],
            "return_adjusted_rank": expected_rank,
            "nonreturn_rank": nonreturn_rank,
            "return_adjusted_chop_line": round(expected_line, 2),
            "nonreturn_chop_line": round(nonreturn_line, 2),
            "return_adjusted_margin": round(my_view["return_adjusted_projection"] - expected_line, 2),
            "nonreturn_margin": round(my_view["nonreturn_projection"] - nonreturn_line, 2),
            "note": (
                "Nonreturn projection uses the same league-scored weekly player projections and all non-return context, "
                "but removes the tracker return-role projection. Treat it as a conservative return-independent anchor, "
                "not a statistical floor. Return-adjusted projection adds the confidence-weighted return model."
            ),
            "my_starters": my_view["starters"],
        }
        reconciliation[lid] = payload

        league_report = (report.get("leagues") or {}).get(lid)
        if league_report is not None:
            league_report["return_adjusted_projection"] = payload["return_adjusted_projection"]
            league_report["projected_return_points_added"] = payload["projected_return_points_added"]
            league_report["nonreturn_projection"] = payload["nonreturn_projection"]
            league_report["return_adjusted_chop_line"] = payload["return_adjusted_chop_line"]
            league_report["nonreturn_chop_line"] = payload["nonreturn_chop_line"]
            league_report["return_adjusted_margin"] = payload["return_adjusted_margin"]
            league_report["nonreturn_margin"] = payload["nonreturn_margin"]
            league_report["return_adjusted_rank"] = payload["return_adjusted_rank"]
            league_report["nonreturn_rank"] = payload["nonreturn_rank"]
            league_report["projection_reconciliation_note"] = payload["note"]

        for row in matchup_summary:
            if str(row.get("league_id")) != lid:
                continue
            row["return_adjusted_projection"] = payload["return_adjusted_projection"]
            row["projected_return_points_added"] = payload["projected_return_points_added"]
            row["nonreturn_projection"] = payload["nonreturn_projection"]
            row["return_adjusted_chop_line"] = payload["return_adjusted_chop_line"]
            row["nonreturn_chop_line"] = payload["nonreturn_chop_line"]
            row["return_adjusted_margin"] = payload["return_adjusted_margin"]
            row["nonreturn_margin"] = payload["nonreturn_margin"]
            row["return_adjusted_rank"] = payload["return_adjusted_rank"]
            row["nonreturn_rank"] = payload["nonreturn_rank"]
            row["score_mode"] = "league-scored weekly projection + separate return reconciliation"

    SUMMARY.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    write_csv("matchup_summary.csv", matchup_summary)
    (ROOT / "data" / "chopped_projection_reconciliation.json").write_text(
        json.dumps({"week": report.get("week"), "leagues": reconciliation}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps({"chopped_leagues_reconciled": len(reconciliation)}, indent=2))


if __name__ == "__main__":
    main()

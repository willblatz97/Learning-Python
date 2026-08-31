from __future__ import annotations

import csv
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
DB = ROOT / "data" / "fantasy_tracker.sqlite"


def read_csv(name: str) -> list[dict]:
    p = OUT / name
    if not p.exists():
        return []
    with p.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(name: str, rows: list[dict]):
    if not rows:
        return
    fields = []
    seen = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k); fields.append(k)
    with (OUT / name).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def replace_table(name: str, rows: list[dict]):
    con = sqlite3.connect(DB)
    try:
        con.execute(f'DROP TABLE IF EXISTS "{name}"')
        if rows:
            fields = list(rows[0].keys())
            defs = ", ".join(f'"{c}" TEXT' for c in fields)
            cols = ",".join(f'"{c}"' for c in fields)
            qs = ",".join("?" for _ in fields)
            con.execute(f'CREATE TABLE "{name}" ({defs})')
            con.executemany(
                f'INSERT INTO "{name}" ({cols}) VALUES ({qs})',
                [[None if r.get(c) is None else str(r.get(c)) for c in fields] for r in rows],
            )
        con.commit()
    finally:
        con.close()


def num(v, default=0.0):
    try: return float(v)
    except (TypeError, ValueError): return default


def inum(v, default=0):
    try: return int(float(v))
    except (TypeError, ValueError): return default


def league_kind(label: str) -> str:
    s = str(label or "").lower()
    if "chopped" in s: return "chopped"
    if "dynasty" in s: return "dynasty"
    if "keeper" in s: return "keeper"
    return "redraft"


def tier(score: float) -> str:
    if score >= 85: return "MUST ADD"
    if score >= 74: return "HIGH"
    if score >= 62: return "MEDIUM"
    if score >= 50: return "SPECULATIVE"
    return "WATCH"


def faab_band(score: float, kind: str) -> tuple[int, int]:
    if score >= 85: low, high = 20, 30
    elif score >= 75: low, high = 12, 20
    elif score >= 65: low, high = 7, 12
    elif score >= 55: low, high = 3, 7
    elif score >= 45: low, high = 1, 3
    else: low, high = 0, 1
    if kind == "chopped":
        low = min(50, math.ceil(low * 1.4))
        high = min(60, math.ceil(high * 1.5))
    return low, high


def confidence_factor(v: str) -> float:
    s = str(v or "").upper()
    return 1.0 if s == "HIGH" else 0.8 if s == "MEDIUM" else 0.6


def main():
    adds = read_csv("waiver_candidates.csv")
    boosts = read_csv("league_return_boosts.csv")
    lookup = {(str(r.get("league_id")), str(r.get("sleeper_id"))): r for r in boosts}

    changed = 0
    for r in adds:
        key = (str(r.get("league_id")), str(r.get("player_id")))
        b = lookup.get(key)
        if not b:
            continue
        ret = num(b.get("return_projection_points"))
        if ret <= 0:
            continue
        conf = str(b.get("confidence") or "LOW")
        dual = str(b.get("dual_role_offense_return")).lower() == "true"
        factor = confidence_factor(conf)
        applied_pts = round(ret * factor, 2)
        score_add = min(25.0, applied_pts * 1.6) + (4.0 if dual else 0.0)
        old = num(r.get("waiver_score"))
        new = round(min(100.0, old + score_add), 1)
        r["waiver_score_before_return"] = old
        r["return_projection_points"] = ret
        r["return_points_confidence_adjusted"] = applied_pts
        r["return_roles"] = b.get("return_roles")
        r["return_confidence"] = conf
        r["dual_role_offense_return"] = b.get("dual_role_offense_return")
        r["offensive_depth_rank"] = b.get("offensive_depth_rank")
        r["waiver_score"] = new
        r["priority"] = tier(new)
        kind = league_kind(r.get("league"))
        lo, hi = faab_band(new, kind)
        remain = inum(r.get("faab_remaining"))
        r["faab_low_pct"] = lo; r["faab_high_pct"] = hi
        r["faab_low"] = math.ceil(remain * lo / 100)
        r["faab_high"] = math.ceil(remain * hi / 100)
        reason = f"return role {b.get('return_roles')} +{applied_pts:g} pts/g"
        if dual:
            reason += " | offense+return dual role"
        existing = str(r.get("reasons") or "")
        r["reasons"] = (existing + " | " if existing else "") + reason
        changed += 1

    grouped = defaultdict(list)
    for r in adds:
        grouped[str(r.get("league_id"))].append(r)
    final = []
    for lid, rows in grouped.items():
        rows.sort(key=lambda x: (-num(x.get("waiver_score")), str(x.get("position") or ""), str(x.get("player") or "")))
        for i, r in enumerate(rows[:30], 1):
            r["league_rank"] = i
            final.append(r)

    write_csv("waiver_candidates.csv", final)
    replace_table("waiver_candidates", final)
    print(json.dumps({"waiver_rows": len(final), "return_adjusted_rows": changed}, indent=2))


if __name__ == "__main__":
    main()

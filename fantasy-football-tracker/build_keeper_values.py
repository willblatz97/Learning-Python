from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "normalized"
DATA = ROOT / "data"
RULES = ROOT / "keeper_rules.json"
LEAGUE_ID = "1359546418284494848"


def read_csv(name: str) -> list[dict]:
    p = OUT / name
    if not p.exists(): return []
    with p.open("r", newline="", encoding="utf-8") as f: return list(csv.DictReader(f))


def num(v, d=None):
    try: return float(v)
    except (TypeError, ValueError): return d


def truth(v): return str(v).lower() in {"true", "1", "yes"}


def expected_round(ecr, teams=12, max_round=17):
    e = num(ecr, None)
    if e is None or e <= 0: return None
    return max(1, min(max_round, int(math.ceil(e / teams))))


def main():
    rules = json.loads(RULES.read_text(encoding="utf-8"))[LEAGUE_ID]
    leagues = {str(r["league_id"]): r for r in read_csv("leagues.csv")}
    league = leagues.get(LEAGUE_ID, {})
    rosters = [r for r in read_csv("rosters.csv") if str(r.get("league_id")) == LEAGUE_ID]
    ownership = [r for r in read_csv("ownership.csv") if str(r.get("league_id")) == LEAGUE_ID]
    picks = [r for r in read_csv("draft_picks.csv") if str(r.get("league_id")) == LEAGUE_ID]
    traded = [r for r in read_csv("traded_picks.csv") if str(r.get("league_id")) == LEAGUE_ID]
    ranks = read_csv("external_rankings.csv")
    idp = {str(r.get("sleeper_id")): r for r in read_csv("idp_values.csv")}

    redraft = {}
    for r in ranks:
        if str(r.get("ecr_type")) == "ro": redraft[str(r.get("sleeper_id"))] = r

    my = next((r for r in rosters if truth(r.get("is_my_roster"))), None)
    my_roster_id = str(my.get("roster_id")) if my else "1"
    draft_rounds = 17
    teams = 12

    drafted_round = {}
    drafted_name = {}
    for p in picks:
        pid = str(p.get("player_id") or "")
        if not pid: continue
        rnd = int(num(p.get("round"), 0) or 0)
        if rnd:
            drafted_round[pid] = rnd
            drafted_name[pid] = str(p.get("full_name") or "")

    known = {str(k): int(v) for k, v in (rules.get("known_keeper_costs") or {}).items()}
    keeper_rows = []
    for r in ownership:
        pid = str(r.get("player_id") or "")
        name = str(r.get("full_name") or "")
        pos = str(r.get("position") or "")
        owner_roster = str(r.get("roster_id") or "")
        cost = known.get(name)
        source = "known override" if cost is not None else None
        if cost is None and pid in drafted_round:
            cost = drafted_round[pid]; source = "draft round"
        if cost is None:
            cost = int(rules.get("free_agent_keeper_round") or 7); source = "FA/default round"

        ecr = num(redraft.get(pid, {}).get("ecr"), None)
        market_round = expected_round(ecr, teams, draft_rounds)
        surplus = (cost - market_round) if market_round is not None else None
        idp_proxy = num(idp.get(pid, {}).get("idp_projection_proxy"), None)
        talent = 0.0 if ecr is None else max(0.0, 25.0 - ecr * 0.08)
        if pos in {"DL", "LB", "DB"} and idp_proxy is not None:
            talent = min(25.0, idp_proxy * 1.5)
        surplus_score = (surplus or 0) * 6.0
        keeper_score = round(talent + surplus_score, 1)
        keeper_rows.append({
            "league_id": LEAGUE_ID, "roster_id": owner_roster, "is_my_roster": r.get("is_my_roster"),
            "player_id": pid, "player": name, "position": pos, "nfl_team": r.get("nfl_team"),
            "keeper_round": cost, "keeper_cost_source": source, "redraft_ecr": ecr,
            "market_round": market_round, "round_surplus": surplus, "keeper_value_score": keeper_score,
            "idp_projection_proxy": idp_proxy,
        })

    # Before the draft, known keepers may not appear in ownership yet. Preserve them explicitly.
    owned_names = {r["player"] for r in keeper_rows}
    for name, cost in known.items():
        if name in owned_names: continue
        pid = next((str(r.get("sleeper_id")) for r in ranks if str(r.get("player") or "") == name), None)
        rr = redraft.get(pid or "", {})
        ecr = num(rr.get("ecr"), None); market_round = expected_round(ecr, teams, draft_rounds)
        surplus = (cost - market_round) if market_round is not None else None
        keeper_rows.append({
            "league_id": LEAGUE_ID, "roster_id": my_roster_id, "is_my_roster": "True", "player_id": pid,
            "player": name, "position": rr.get("position"), "nfl_team": rr.get("team"), "keeper_round": cost,
            "keeper_cost_source": "known override (pre-draft)", "redraft_ecr": ecr, "market_round": market_round,
            "round_surplus": surplus, "keeper_value_score": round((surplus or 0) * 6 + (0 if ecr is None else max(0,25-ecr*.08)),1),
            "idp_projection_proxy": None,
        })

    keeper_rows.sort(key=lambda r: -(num(r.get("keeper_value_score"), -999) or -999))
    with (OUT / "keeper_values.csv").open("w", newline="", encoding="utf-8") as f:
        fields = list(keeper_rows[0]) if keeper_rows else ["league_id","player"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(keeper_rows)

    # Future pick inventory: every original roster owns its pick unless a traded-pick row says otherwise.
    seasons = sorted({2026, 2027, 2028} | {int(num(r.get("season"), 0) or 0) for r in traded if num(r.get("season"),0)})
    trade_map = {(int(num(r.get("season"),0)), int(num(r.get("round"),0)), str(r.get("roster_id"))): str(r.get("owner_id")) for r in traded}
    inventory = []
    for season in seasons:
        if season < 2026: continue
        for rnd in range(1, draft_rounds + 1):
            current_owner = trade_map.get((season, rnd, my_roster_id), my_roster_id)
            if current_owner == my_roster_id:
                inventory.append({"season": season, "round": rnd, "original_roster_id": my_roster_id, "source": "own"})
            for (s, rr, original), owner in trade_map.items():
                if s == season and rr == rnd and owner == my_roster_id and original != my_roster_id:
                    inventory.append({"season": season, "round": rnd, "original_roster_id": original, "source": "acquired"})

    my_keeper_rounds = sorted({int(r["keeper_round"]) for r in keeper_rows if truth(r.get("is_my_roster")) and r.get("keeper_round")})
    collisions = []
    for season in [s for s in seasons if s >= 2027]:
        owned_rounds = {int(x["round"]) for x in inventory if int(x["season"]) == season}
        for rnd in my_keeper_rounds:
            if rnd not in owned_rounds:
                collisions.append({"season": season, "keeper_round": rnd, "note": "No pick currently controlled in this keeper round; confirm league enforcement before trading around this slot."})

    result = {
        "league_id": LEAGUE_ID, "league": rules.get("league"), "status": league.get("status"),
        "rules": rules, "my_roster_id": my_roster_id,
        "my_keeper_values": [r for r in keeper_rows if truth(r.get("is_my_roster"))][:12],
        "all_keeper_values": keeper_rows,
        "my_future_pick_inventory": inventory,
        "keeper_pick_collision_flags": collisions,
        "valuation_note": "Round surplus = locked keeper round minus estimated 12-team market round. Positive surplus means the player is cheaper to keep than his market draft cost. IDP players use this league's scoring-based IDP proxy when available."
    }
    (DATA / "keeper_values.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    lines = ["# League Is Rigged V2 — Keeper Economy", "", result["valuation_note"], "",
             f"Rules: 2 keepers; fixed round forever; FA pickup = R{rules.get('free_agent_keeper_round')}; future picks tradable; IDP = 1 DL / 1 LB / 1 DB.", ""]
    for r in result["my_keeper_values"]:
        lines.append(f"- {r['player']} ({r.get('position')}) — keeper R{r['keeper_round']}; market R{r.get('market_round') or '?'}; surplus {r.get('round_surplus') if r.get('round_surplus') is not None else '?'} rounds; score {r['keeper_value_score']}")
    lines += ["", "## Future picks currently controlled"]
    for season in sorted(set(int(x["season"]) for x in inventory)):
        vals = [x for x in inventory if int(x["season"]) == season]
        own = ", ".join(f"R{x['round']}" + (f"(from {x['original_roster_id']})" if x['source']=='acquired' else "") for x in vals)
        lines.append(f"- {season}: {own}")
    if collisions:
        lines += ["", "## Keeper/pick collision watch"] + [f"- {x['season']} R{x['keeper_round']}: {x['note']}" for x in collisions]
    (DATA / "keeper_values.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"keeper_rows": len(keeper_rows), "my_keeper_rows": len(result['my_keeper_values']), "pick_inventory_rows": len(inventory), "collision_flags": len(collisions)}, indent=2))


if __name__ == "__main__":
    main()

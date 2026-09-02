# Fantasy Football Tracker / War Room

Automated multi-league fantasy system for Sleeper user **blatzzy** and five 2026 leagues.

## Front doors

- **Visual War Room:** `data/war_room.html`
- **Mobile / GitHub War Room:** `data/war_room.md`
- **Command Center:** `data/command_center.md`
- **Tuesday waiver board:** generated from the shared database and scoring engine
- **Saturday lineup board:** generated from the same shared database and scoring engine
- **Trade Target Board:** `data/trade_targets.md`
- **Injury Ripple Report:** `data/injury_ripple_report.md`

The chat reports are the concise action layer. The War Room is the deeper visual layer.

## Configured leagues

- 1389740418316374016 — 10-Team Redraft / Big Tiger Takeover
- 1370453762442797056 — 18-Team Chopped / Surviving the Chamber
- 1370218620990263296 — 12-Team Dynasty / One League to Rule Them All
- 1366076769534251008 — 10-Team Dynasty / Dynasty V2
- 1359546418284494848 — 12-Team 2-Keeper / League Is Rigged V2

## Refresh cadence

GitHub Actions refreshes the complete intelligence stack twice weekly:

- Tuesday before the waiver/cut report
- Saturday before the lineup/matchup report

A manual workflow dispatch is also supported.

Latest manual refresh request: **2026-09-02 post-waiver Wednesday**.

## Intelligence stack

The pipeline currently combines:

- Sleeper league settings, scoring, rosters, ownership, starters, matchups and transactions
- current consensus rankings / projections when available
- player exposure across every league
- current snap and usage trends, with prior-year usage blocked from altering current-season scores
- offensive-line starter health
- game-time weather
- kick and punt return roles with league-specific return scoring
- offensive injury opportunity redistribution
- defensive injury matchup ripples for CB, safety, linebacker, edge and interior defensive line
- start/sit and matchup recomputation after projection adjustments
- chopped-league cutoff / survival analysis
- playoff-push and rebuild trade-target models

## Injury ripple philosophy

The injury engine does more than downgrade the injured player. It follows the fantasy consequences.

Examples include RB1 injury -> RB2 workload, WR1 injury -> WR2/WR3/TE target share, QB1 injury -> backup relevance plus pass-catcher efficiency risk, CB1 injury -> projected WR1 coverage improvement, safety injury -> WR/TE deep-middle improvement, linebacker injury -> TE/RB improvement, edge injury -> reduced QB pressure, and interior-DL injury -> rushing improvement.

Soft Questionable/DNP/Limited designations are damped until official weekly injury reports exist. OUT/IR/PUP remain high-impact signals. CB1-to-WR1 effects are treated as matchup leverage unless a confirmed shadow assignment is available.

## Trade philosophy

Trade targets are not simply a list of the best players in the league. The engine weighs:

- your positional weakness
- current fantasy usefulness
- redraft versus dynasty market value
- age curve
- likely acquisition cost
- seller positional surplus
- seller record once the season develops
- win-now value arbitrage for contenders
- youth / long-term value arbitrage for rebuilders

Dynasty leagues stay on a dual-track push/rebuild board early in the season. Once record and roster strength provide enough evidence, the model can emphasize one direction.

## Data layout

`data/raw/` contains direct API/source responses.

`data/normalized/` contains analysis-ready CSV tables and derived intelligence layers.

`data/fantasy_tracker.sqlite` is the shared query layer.

`data/summary.json` provides the latest account/league sanity check.

## Design rule

Sleeper player ID and league ID are the permanent keys. Player information is normalized once and referenced across all leagues so ownership, exposure, availability, injury impact, trade value, start/sit, waivers, returns, weather, OL health and chopped-league analysis share one source of truth.

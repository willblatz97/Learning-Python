# Fantasy Football Tracker Foundation

Automated Sleeper data foundation for Sleeper user **blatzzy** and five 2026 leagues.

## Configured leagues

- 1389740418316374016
- 1370453762442797056
- 1370218620990263296
- 1366076769534251008
- 1359546418284494848

## Refresh cadence

GitHub Actions refreshes Sleeper data twice weekly:

- Tuesday before the waiver/cut report
- Saturday before the lineup/matchup report

A manual workflow dispatch is also supported.

## Data pulled

For the account and every configured league, the ingestion job pulls as much public Sleeper data as is useful for the foundation:

- NFL season/state metadata
- Sleeper account identity
- 2026 leagues attached to the account
- league metadata and status
- complete scoring settings
- roster-position configuration
- waiver/FAAB/playoff/general league settings
- league users / team names
- all rosters and roster settings
- starters, reserve and taxi assignments
- player ownership across every roster
- player NFL team, position, injury/practice/depth-chart fields
- traded future picks
- league drafts
- draft picks and player metadata
- current/adjacent-week matchups
- current/recent transactions, adds, drops, waiver budget and trades
- complete Sleeper NFL player dictionary

## Output layout

`data/raw/` contains direct Sleeper API responses.

`data/normalized/` contains analysis-ready CSV tables:

- `leagues.csv`
- `owners.csv`
- `rosters.csv`
- `ownership.csv`
- `traded_picks.csv`
- `drafts.csv`
- `draft_picks.csv`
- `matchups.csv`
- `transactions.csv`

`data/fantasy_tracker.sqlite` mirrors the normalized tables for querying and future report generation.

`data/summary.json` provides the latest account/league sanity check.

## Design rule

Sleeper player ID and league ID are the permanent keys. Player information is normalized once and referenced across all five leagues so cross-league ownership, exposure, availability, injury impact, start/sit, waiver and chopped-league analysis can share one foundation.

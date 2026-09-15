# Fantasy War Room — Week 1 Model Audit

Audit date: 2026-09-15

This is intentionally a light calibration review. Week 1 is too small a sample for aggressive weight changes. The larger model audit is planned after Week 4, when 2026 snap, route, target, carry, return, injury and matchup samples are materially stronger.

## Audit source

Pregame projections are frozen from commit `f8000682ddffffc167827d5d09858375ce907b0c` (Sept. 8, before Week 1 games). Actual scores come from the Sept. 15 post-Week-1 Sleeper refresh. This avoids comparing results against projections rebuilt with hindsight.

## User-team total accuracy

| League | Our pregame | Sleeper baseline | Actual | Our error | Sleeper error |
|---|---:|---:|---:|---:|---:|
| Big Tiger Takeover | 149.71 | 144.71 | 133.16 | +16.55 | +11.55 |
| One League to Rule Them All | 152.28 | 145.45 | 156.46 | -4.18 | -11.01 |
| Dynasty V2 | 157.97 | 153.37 | 172.76 | -14.79 | -19.39 |
| League Is Rigged V2 | 174.58 | 168.18 | 112.12 | +62.46 | +56.06 |

Complete-projection MAE across these four leagues:
- War Room: **24.50 points**
- Sleeper baseline: **24.50 points**

Week 1 verdict: essentially tied on raw team-total error. The League Is Rigged outlier dominates both models' error.

Surviving the Chamber is excluded from the Sleeper MAE comparison because Sleeper only supplied usable projections for 8 of 9 starters. Our projection was 142.31; actual was 116.46.

## H2H matchup-margin accuracy

| League | Our projected margin | Sleeper margin | Actual margin | Our abs. margin error | Sleeper abs. margin error |
|---|---:|---:|---:|---:|---:|
| Big Tiger Takeover | -0.47 | -0.32 | -27.26 | 26.79 | 26.94 |
| One League to Rule Them All | -17.77 | -21.19 | -14.64 | 3.13 | 6.55 |
| Dynasty V2 | +2.91 | +2.96 | +3.00 | 0.09 | 0.04 |
| League Is Rigged V2 | -8.06 | -3.90 | -34.70 | 26.64 | 30.80 |

- War Room matchup-margin MAE: **14.16 points**
- Sleeper matchup-margin MAE: **16.08 points**
- War Room correct side/winner direction: **4 of 4**
- Sleeper correct side/winner direction: **4 of 4**

Week 1 verdict: the War Room contextual layers helped relative matchup evaluation more than absolute scoring. That is encouraging, but one week is not enough to increase their weight.

## Chopped-league read

War Room projected Masta's at 142.31 with a 39.94-point cushion over the modeled danger line. Actual score was 116.46. The actual low score was about 107.40, leaving roughly a 9.06-point real cushion.

Important nuance: the projected cut-line itself was close (about 102.37 vs ~107.40 actual); most of the safety-margin miss came from overprojecting our lineup, not from misunderstanding the league's danger line.

The return model should NOT be torn down from this result. Two key return-driven starters illustrate the volatility:
- Myles Price: projected 11.10, actual 19.20 — model was low.
- Jacob Saylors: projected 12.99, actual 2.50 — model was high.
- Combined: 24.09 projected vs 21.70 actual, only 2.39 high.

Return-yard projections remain a watch item because individual return opportunity is volatile, but Week 1 did not show a broad systematic return-model failure.

## League Is Rigged outlier

Both systems badly overprojected Lagrange 66ers: War Room 174.58, Sleeper 168.18, actual 112.12.

This was not primarily a keeper-value problem; keeper value had already been removed from weekly scoring. Several core offensive projections missed together:
- Drake Maye: 27.74 projected vs 18.92 actual.
- Quinshon Judkins: 15.93 vs 7.50.
- Ja'Marr Chase: 21.57 vs 3.20.
- Drake London: 17.05 vs 5.50.
- Jaylen Warren: 17.16 vs 11.80.

IDP was mixed rather than universally inflated:
- Jared Verse: 7.99 projected vs 9.00 actual.
- Jack Campbell: 20.39 vs 14.00 actual.

There was also a late lineup difference between the frozen pregame snapshot and final starters, so Week 1 should not be used to aggressively recalibrate the League Is Rigged scoring engine. The main lesson is uncertainty: custom-scoring totals can still have very wide weekly variance even when the scoring conversion itself is correct.

## 2026 usage layer

2026 usage is now active. Current start/sit code already applies conservative sample-size damping:
- 1-game sample factor: 40%
- 2-game sample factor: 65%
- 3+ games: full sample factor
- RB/WR/TE role adjustment factor: 90%; QB: 45%
- weekly usage adjustment cap: +/-2.75 fantasy points

No change recommended after Week 1. One game should nudge priors, not replace them.

## Structural correction made after Week 1

The projected-finish engine had been using one current week's matchup-specific roster projection as if it were a fully reliable season-long power rating. That can create false certainty after only one game and can make playoff odds swing far too aggressively.

The weekly start/sit projection model remains unchanged. Only season-outlook simulations now shrink weekly roster-power differences toward league average while the 2026 sample is small:
- Week 2: 45% season-power confidence
- Week 3: 60%
- Week 4: 75%
- Week 5: 90%
- Week 6+: 100%

This is a confidence correction, not a reaction to which players happened to boom or bust in Week 1.

## Watch list through Week 4

1. **League-context additive bias.** Our normal-league projections started roughly 4-7 points above the Sleeper baseline. Week 1 was mixed: this helped One League and Dynasty V2, but hurt Big Tiger and League Is Rigged. Do not change yet; track signed error through Week 4.
2. **IDP calibration.** Track DL/LB/DB separately in League Is Rigged. Do not use keeper or draft cost in IDP valuation.
3. **Return opportunity.** Compare projected kick/punt volume with actual return volume, not only fantasy points.
4. **Usage signal.** Preserve one-game damping; evaluate whether 40%/65% sample factors are appropriate after four weeks.
5. **Injury/ripple layers.** Track whether small contextual adjustments improve matchup margin without creating positive scoring bias.
6. **Season outlook stability.** Confirm early-season confidence shrinkage prevents extreme playoff-odds swings while still reacting meaningfully to real role changes.

## Week 1 decision

Do **not** globally retune projection weights from one weekend. Preserve the current weekly model, keep 2026 usage damped, correct only structural/implementation issues, and accumulate Weeks 1-4 accuracy data for the larger audit.

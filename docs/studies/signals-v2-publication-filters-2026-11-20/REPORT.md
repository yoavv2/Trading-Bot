# Publication-filter study: signals-v2-publication-filters-2026-11-20

Status: `completed`. Protocol SHA-256 `7ea1e4521f03bcb0272ad21ca378a48ef4b64f383d81b0fa155f1246d6106ac8`. Code `b9c0320eed0a17f480fb1b1e4fb72732221736d3`.

## Setup

- Version: `time_series_momentum_daily` (`a4bbd0b8-b5ea-45b3-b59e-abafd810cb0f`, specification `dea864251cc077719c7f81f40f727a6f8e590f06664b089b102bcc74e87c9365`).
- Source: `docs/studies/signals-v2-wide-2026-11-19/results.json` (SHA-256 `818755ad3d9612276c3518bf0ac124a8fabca1e16955483e2821f440975c408c`), study `59489ef0-fec9-4c26-b9aa-59f99168e53c`. Its stored signals and trades are read; no backtest is run.
- Assets: 42. Costs as in the source study (5 bps slippage on each fill).
- Development: 2015-01-02 to 2022-10-06; 2962 entry signals in 42 runs (0 more had no outcome at the window's end and are left out).
- Validation: 2022-10-07 to 2024-10-04; 774 entry signals in 42 runs (0 more had no outcome at the window's end and are left out).
- The source study's holdout is not read.
- Target rate: 7 to 12 published trades a month.
- Each rule is applied on its own. A signal a rule holds back is never published later.

## Reading

Keeps the edge: mean net R above 0 in every window, and development total net R at least the development R drawdown. Meets the rate: published trades a month inside the target in every window. Rules are listed by the lower of their windows' mean net R.

| Rule | Keeps the edge | Meets the rate | Lower mean net R |
|---|---|---|---|
| min_initial_risk_3pct | yes | no | 0.124 |
| open_cap_5 | yes | no | 0.118 |
| min_initial_risk_4pct | yes | no | 0.116 |
| min_initial_risk_2pct | yes | no | 0.112 |
| all_signals | yes | no | 0.103 |
| open_cap_12 | yes | no | 0.095 |
| open_cap_10 | yes | yes | 0.090 |
| open_cap_8 | no | yes | 0.072 |

## Development window

| Rule | Published trades/mo | Closed | Held back / withdrawn | Published, expired | Most open at once | Mean net R | Median net R | Total net R | Win rate | Max R drawdown | Best / worst R | Profitable assets |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| min_initial_risk_3pct | 25.65 | 2378 | 124 | 449 | 42 | 0.124 | -0.308 | 294.25 | 37.8% | 89.97 | 4.53 / -3.13 | 34 of 42 |
| open_cap_5 | 4.17 | 383 | 2477 | 97 | 5 | 0.118 | -0.446 | 45.22 | 37.1% | 34.24 | 5.40 / -4.20 | 27 of 42 |
| min_initial_risk_4pct | 23.53 | 2181 | 321 | 449 | 42 | 0.116 | -0.284 | 253.37 | 38.4% | 80.99 | 4.17 / -3.13 | 36 of 42 |
| min_initial_risk_2pct | 26.48 | 2455 | 47 | 449 | 42 | 0.112 | -0.333 | 275.71 | 37.2% | 125.22 | 5.75 / -3.13 | 31 of 42 |
| all_signals | 26.98 | 2502 | 0 | 449 | 42 | 0.103 | -0.355 | 257.13 | 36.7% | 147.34 | 10.01 / -4.20 | 29 of 42 |
| open_cap_12 | 10.90 | 1006 | 1697 | 250 | 12 | 0.095 | -0.589 | 95.46 | 36.1% | 72.31 | 10.01 / -4.20 | 24 of 42 |
| open_cap_10 | 9.02 | 832 | 1903 | 219 | 10 | 0.090 | -0.583 | 74.49 | 36.1% | 70.32 | 9.30 / -4.20 | 24 of 42 |
| open_cap_8 | 7.15 | 659 | 2118 | 178 | 8 | 0.072 | -0.569 | 47.47 | 35.7% | 56.44 | 5.40 / -4.20 | 27 of 42 |

## Validation window

| Rule | Published trades/mo | Closed | Held back / withdrawn | Published, expired | Most open at once | Mean net R | Median net R | Total net R | Win rate | Max R drawdown | Best / worst R | Profitable assets |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| min_initial_risk_3pct | 29.35 | 667 | 18 | 53 | 38 | 0.176 | -0.129 | 117.44 | 42.1% | 61.45 | 3.83 / -2.45 | 31 of 42 |
| open_cap_5 | 4.59 | 105 | 654 | 10 | 5 | 0.270 | -0.229 | 28.32 | 40.0% | 15.12 | 10.45 / -1.65 | 17 of 32 |
| min_initial_risk_4pct | 28.14 | 638 | 47 | 53 | 38 | 0.184 | -0.111 | 117.58 | 42.5% | 52.32 | 3.47 / -2.45 | 32 of 42 |
| min_initial_risk_2pct | 29.81 | 678 | 7 | 53 | 39 | 0.211 | -0.116 | 143.30 | 42.6% | 41.00 | 7.11 / -2.45 | 34 of 42 |
| all_signals | 30.10 | 685 | 0 | 53 | 39 | 0.224 | -0.127 | 153.53 | 42.5% | 41.00 | 10.45 / -2.45 | 35 of 42 |
| open_cap_12 | 12.11 | 278 | 464 | 20 | 12 | 0.217 | -0.161 | 60.31 | 41.4% | 28.51 | 10.45 / -2.45 | 30 of 41 |
| open_cap_10 | 10.40 | 239 | 507 | 18 | 10 | 0.237 | -0.164 | 56.67 | 42.3% | 27.38 | 10.45 / -2.45 | 27 of 41 |
| open_cap_8 | 8.60 | 198 | 555 | 13 | 8 | 0.205 | -0.165 | 40.62 | 40.9% | 23.74 | 10.45 / -2.45 | 25 of 39 |

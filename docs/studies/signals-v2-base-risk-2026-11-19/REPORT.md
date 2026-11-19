# Signals study: signals-v2-base-risk-2026-11-19

Status: `completed`. Protocol SHA-256 `6b9bb9a041fd0326979a3815ada7a129f20c805b34ff6c8ab624a5d61b081ac1`. Code `0a7d8e62c13338899a3996b409c7c95acbb0bcfe-dirty`.

## Setup

- Assets: NVDA, PLTR, QQQ, SMCI, URA (tiingo, adjusted bars).
- Development: 2021-10-07 to 2024-10-04.
- Validation: 2024-10-07 to 2025-10-06.
- Holdout: 2025-10-07 to 2026-10-07.
- Costs: 5 bps slippage on each fill, 1 USD per order; capital 100000, fractional quantity, one asset per run.
- Study `9afa768e-2139-4744-941f-9d2663d9b229`, revision `8a417fe8-386e-4cdc-8963-1752fedbaa35`.

| Version | Version id | Specification SHA-256 |
|---|---|---|
| trend_following_daily | `9153763e-9e6e-4c66-b2e9-5ab913b9520b` | `2a52b507621cbfa8bb4fbc82426b60fd3e608df5810cbab02991256d4ebeb9ba` |
| rsi_mean_reversion_daily | `98348948-47be-4e64-a958-c6db0051aaa7` | `acd119da1ded873328c9d04b2a7819d62d386292c3d5f52c115d851776339cd1` |
| donchian_breakout_daily | `5a4448de-cd95-4d46-b175-ec7709f3bf24` | `aecf590af33d99a358e2c4791c106047872d0a56dd186fe3cb2cfd0b3fbbc94d` |
| time_series_momentum_daily | `a4bbd0b8-b5ea-45b3-b59e-abafd810cb0f` | `dea864251cc077719c7f81f40f727a6f8e590f06664b089b102bcc74e87c9365` |

## Daily engine against the backtest

45 runs checked, 1686 signals and 1139 closed trades compared, **0 differences**. 0 runs not checked.

## Development window, pooled over the assets

| Version | Signals/mo | Trades/mo | Closed | Expired: below stop / above target / reward-risk | Exits: stop / target / rule | Mean net R | Total net R | Mean gross R | Win rate | Max R drawdown | Worst asset equity drawdown | Flagged trades | Total net R if target first |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| trend_following_daily | 11.91 | 8.40 | 300 | 4 / 4 / 118 | 154 / 102 / 44 | 0.458 | 137.41 | 0.489 | 38.0% | 18.90 | -39.1% | 0 (0.0%) | 137.41 |
| rsi_mean_reversion_daily | 1.34 | 0.78 | 28 | 5 / 0 / 15 | 16 / 12 / 0 | 0.411 | 11.50 | 0.443 | 42.9% | 7.45 | -20.1% | 0 (0.0%) | 11.50 |
| donchian_breakout_daily | 3.31 | 2.20 | 79 | 2 / 0 / 38 | 42 / 32 / 5 | 0.404 | 31.91 | 0.435 | 44.3% | 11.56 | -34.8% | 0 (0.0%) | 31.91 |
| time_series_momentum_daily | 18.59 | 12.60 | 449 | 14 / 5 / 196 | 270 / 161 / 18 | 0.363 | 162.96 | 0.415 | 37.0% | 44.28 | -48.1% | 0 (0.0%) | 162.96 |

Total net R per asset:

| Version | NVDA | PLTR | QQQ | SMCI | URA |
|---|---|---|---|---|---|
| trend_following_daily | 20.92 | 69.51 | 7.03 | 46.91 | -6.95 |
| rsi_mean_reversion_daily | -1.46 | 5.10 | -0.82 | 4.03 | 4.65 |
| donchian_breakout_daily | 17.00 | 3.80 | 7.68 | 6.03 | -2.60 |
| time_series_momentum_daily | 0.16 | 82.98 | 2.27 | 78.53 | -0.98 |

## Validation window, pooled over the assets

| Version | Signals/mo | Trades/mo | Closed | Expired: below stop / above target / reward-risk | Exits: stop / target / rule | Mean net R | Total net R | Mean gross R | Win rate | Max R drawdown | Worst asset equity drawdown | Flagged trades | Total net R if target first |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| trend_following_daily | 10.26 | 7.42 | 85 | 2 / 0 / 32 | 38 / 30 / 17 | 0.286 | 24.27 | 0.311 | 38.8% | 15.30 | -33.2% | 0 (0.0%) | 24.27 |
| rsi_mean_reversion_daily | 1.58 | 1.08 | 13 | 5 / 0 / 1 | 8 / 5 / 0 | 0.373 | 4.85 | 0.455 | 38.5% | 6.21 | -24.7% | 0 (0.0%) | 4.85 |
| donchian_breakout_daily | 3.42 | 2.59 | 29 | 1 / 0 / 9 | 15 / 12 / 2 | 0.504 | 14.62 | 0.526 | 44.8% | 3.51 | -15.2% | 0 (0.0%) | 14.62 |
| time_series_momentum_daily | 17.76 | 11.67 | 135 | 11 / 1 / 61 | 80 / 48 / 7 | 0.175 | 23.59 | 0.203 | 37.0% | 20.71 | -50.7% | 0 (0.0%) | 23.59 |

Total net R per asset:

| Version | NVDA | PLTR | QQQ | SMCI | URA |
|---|---|---|---|---|---|
| trend_following_daily | -5.68 | 28.95 | 1.58 | 1.73 | -2.31 |
| rsi_mean_reversion_daily | 0.00 | 0.00 | 6.37 | -3.01 | 1.49 |
| donchian_breakout_daily | 4.39 | 8.80 | 1.94 | -3.06 | 2.56 |
| time_series_momentum_daily | -1.72 | 31.69 | 3.67 | -14.81 | 4.75 |

## Holdout window, pooled over the assets

| Version | Signals/mo | Trades/mo | Closed | Expired: below stop / above target / reward-risk | Exits: stop / target / rule | Mean net R | Total net R | Mean gross R | Win rate | Max R drawdown | Worst asset equity drawdown | Flagged trades | Total net R if target first |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| donchian_breakout_daily | 2.25 | 1.91 | 21 | 0 / 0 / 4 | 12 / 6 / 3 | -0.054 | -1.13 | -0.031 | 28.6% | 7.15 | -15.9% | 0 (0.0%) | -1.13 |

Total net R per asset:

| Version | NVDA | PLTR | QQQ | SMCI | URA |
|---|---|---|---|---|---|
| donchian_breakout_daily | -2.63 | 1.35 | 0.09 | 1.63 | -1.57 |

## Selection

Verdict: `candidate_selected`.

| Version | Qualifies | Ranking value | Failed criteria |
|---|---|---|---|
| donchian_breakout_daily | yes | 0.404 | none |
| trend_following_daily | yes | 0.286 | none |
| time_series_momentum_daily | yes | 0.175 | none |
| rsi_mean_reversion_daily | no | 0.373 | development_sample_too_small, development_too_few_trades_per_month |

## Holdout

Version run on the holdout: `donchian_breakout_daily`. Verdict: `failed`.
Pilot candidate: `None`.

## Runs

| Version | Asset | Window | Run id | Input digest | Results digest | Engine differences |
|---|---|---|---|---|---|---|
| trend_following_daily | NVDA | development | `a94a43a7-e317-4842-8e07-68b7f58811b9` | `706dc5faf93e2e1b` | `14975640028cb8dd` | 0 |
| trend_following_daily | NVDA | validation | `7870079d-a502-42d1-958b-9a321aab5b6b` | `b91966f33f069438` | `cf1beac24cbceb94` | 0 |
| trend_following_daily | PLTR | development | `ba2b608f-0725-47e3-8666-100670df563a` | `7d8a04e01757dcb0` | `c1fc3b60f0b03d77` | 0 |
| trend_following_daily | PLTR | validation | `e094fae7-b870-4ac0-b228-8035b9ec5f02` | `8e9edb677393170c` | `5afbd9c280913cd0` | 0 |
| trend_following_daily | QQQ | development | `b873a9c6-394f-4649-87a8-783d393d913d` | `d71b26f93b27d813` | `5c15dd682cfb52cc` | 0 |
| trend_following_daily | QQQ | validation | `4aac0e97-ac1d-458e-a4c7-d24584ced018` | `c57602dca77e1d5b` | `2b84bd04eb1533a4` | 0 |
| trend_following_daily | SMCI | development | `5884cf93-172d-496f-a198-9610d929fcfa` | `42d67ee38d2df78c` | `52742207ff8beb2a` | 0 |
| trend_following_daily | SMCI | validation | `2c502800-5f5b-4f6c-b87b-8f9fad23cdaa` | `cc64eb1be414371d` | `aa7e7fe45e628e67` | 0 |
| trend_following_daily | URA | development | `fbe11084-e769-442a-9262-fca647e3e145` | `57662ae4d628177e` | `659697a1e2decb18` | 0 |
| trend_following_daily | URA | validation | `1f60fc18-5745-49c1-80dc-0f76899b62b0` | `b0bf34aef6aa480c` | `45f3faee28311290` | 0 |
| rsi_mean_reversion_daily | NVDA | development | `05be3891-9a7a-4303-a0b4-3de75c8b3f06` | `29fc6f787c204f42` | `10b1387cb1c87d07` | 0 |
| rsi_mean_reversion_daily | NVDA | validation | `b9ab9581-0cf2-4b41-ba04-c0e0805084df` | `ab5772451fcefd7d` | `ed10d09186cf4c11` | 0 |
| rsi_mean_reversion_daily | PLTR | development | `3aa0c2c2-21ee-4f60-b458-e19442751136` | `ddb3e7fb04fc5aea` | `62ae0d797fca867c` | 0 |
| rsi_mean_reversion_daily | PLTR | validation | `f9eb0f81-8489-4157-96ff-b20703cf7875` | `5778dc44b4afbafb` | `ed10d09186cf4c11` | 0 |
| rsi_mean_reversion_daily | QQQ | development | `065bbde9-b490-484b-8ed2-31f25f5b62b5` | `b419dc35f160d8dc` | `2134828944959bfc` | 0 |
| rsi_mean_reversion_daily | QQQ | validation | `3b7cb9b1-6e50-4b02-b0b9-2a946f28153c` | `9539b55022e6dde6` | `28934fa0f852deb2` | 0 |
| rsi_mean_reversion_daily | SMCI | development | `ddb8364a-9563-4d2d-aee4-281003bc6ab5` | `5a576fc5cd051ed5` | `9626cb9c504125d6` | 0 |
| rsi_mean_reversion_daily | SMCI | validation | `8a456f79-da78-4e2f-bbae-cd1136b68a31` | `b829a6864b20c226` | `ad77c88bf444f5ba` | 0 |
| rsi_mean_reversion_daily | URA | development | `9d18d072-7dc1-408d-bef1-79bc667f51c6` | `151ef65c510407e9` | `a836f1f82f8221b1` | 0 |
| rsi_mean_reversion_daily | URA | validation | `cfe10b22-7e96-4908-b1d7-a130b0153405` | `0deaa0258450c6f1` | `1d96cf9450092e71` | 0 |
| donchian_breakout_daily | NVDA | development | `909801b6-c860-4fe6-8a36-5c9038836f3e` | `89f4591a00d25788` | `580813566363e35d` | 0 |
| donchian_breakout_daily | NVDA | validation | `eda2b679-64e0-4e3b-9365-f6ab91670f15` | `e2c081fecfbe324d` | `2f2142200e436ffe` | 0 |
| donchian_breakout_daily | PLTR | development | `b2b4fcae-4add-45fa-9a79-988067d18287` | `e0bda409ad25f2c3` | `b67f4d166182a3c3` | 0 |
| donchian_breakout_daily | PLTR | validation | `a10496e6-5aeb-4d13-a166-4524c848ee5a` | `932794f34a343346` | `fa8f058225aa5633` | 0 |
| donchian_breakout_daily | QQQ | development | `124909c3-a45e-4ec2-9924-3d86b8c82c90` | `f68b3a5f68ec30b6` | `f3136173581a3e0f` | 0 |
| donchian_breakout_daily | QQQ | validation | `d362157f-b94f-4cc3-b3c2-e8ba55c82a51` | `ada52474559f8d4c` | `b237f67f0b690750` | 0 |
| donchian_breakout_daily | SMCI | development | `9acd7f9e-6272-4b1d-9197-94ec479aab95` | `d5fc8ac0d23bd23c` | `f87b4b6bd22ad725` | 0 |
| donchian_breakout_daily | SMCI | validation | `22084193-8c24-47d6-b884-8493fe710c33` | `fb7e547d2bb89412` | `d14950c7a8b7bef0` | 0 |
| donchian_breakout_daily | URA | development | `ab2eedfa-e4a3-4044-879f-4e636004a049` | `889a743eed7c9357` | `56efa68e1bdcfbdb` | 0 |
| donchian_breakout_daily | URA | validation | `68d5a946-f856-407d-a455-891e331faa43` | `cc9fe1fe7da3fda7` | `d7e01d1915973799` | 0 |
| time_series_momentum_daily | NVDA | development | `396aec5f-df7b-44ec-998b-02bf9a5573b4` | `ad7419afbbff6f65` | `01ff55ea025ad916` | 0 |
| time_series_momentum_daily | NVDA | validation | `a193cd4f-ab49-4945-83fe-577875c95f09` | `e2e87bdb9e54cfce` | `9b1c6a40924cd29b` | 0 |
| time_series_momentum_daily | PLTR | development | `ae0637f0-1401-4dce-b01d-cf52a6406af1` | `2155c3754e2ff876` | `398bd161ae15c4ad` | 0 |
| time_series_momentum_daily | PLTR | validation | `8ba9ef45-d93c-45cf-9087-2aafd40536e6` | `75b4dc45a761b97f` | `e13d17d0c5d1ca18` | 0 |
| time_series_momentum_daily | QQQ | development | `088d22b9-fc1b-47f6-a85d-c8ce2eeb4a36` | `7246bcf96c92acdd` | `73adc8e75c122a2b` | 0 |
| time_series_momentum_daily | QQQ | validation | `935b7ebe-753e-4b88-a66a-cbb1976b44c7` | `1591c38a027ca5e5` | `62427113b349f772` | 0 |
| time_series_momentum_daily | SMCI | development | `9cd4322b-7dad-44d9-aad9-567f2f700c75` | `01cd050cacfeca4a` | `d662de95d89e2742` | 0 |
| time_series_momentum_daily | SMCI | validation | `79b7a567-379d-4d0f-9e5f-5510ac4d74b0` | `b59cec953d61f82b` | `4c900070f1f1517e` | 0 |
| time_series_momentum_daily | URA | development | `3b369713-74d1-474a-b1a5-97991c390b67` | `73e2caf513776337` | `41435a132741da3c` | 0 |
| time_series_momentum_daily | URA | validation | `e6764008-3f3a-44e0-8c6d-aacbe3809f43` | `b5cca8f5fd9e1c6b` | `2f43e089411ff3d0` | 0 |
| donchian_breakout_daily | NVDA | final_test | `ae153ce1-b54e-478e-bb63-8c6c8a4fc6e3` | `7d48c5f32054a56e` | `c8d9e1673124c3b4` | 0 |
| donchian_breakout_daily | PLTR | final_test | `a5c316d7-fba5-4188-aa9d-edf20c8df3e4` | `d384ea02ecd34fdf` | `eb39f35a317a0a96` | 0 |
| donchian_breakout_daily | QQQ | final_test | `c9c3f3b4-6b91-49ee-9bca-8e2693f216a5` | `21e40aa6a1a5de0b` | `dd60e5d965c905eb` | 0 |
| donchian_breakout_daily | SMCI | final_test | `fac72844-3d68-4ca7-981a-d4d07015fcd1` | `eb6ce78dcfd19b14` | `4809bd872ac60307` | 0 |
| donchian_breakout_daily | URA | final_test | `f94ea076-ed2c-4cdc-a871-c1ba5dc29378` | `3d062a3d6c406cf7` | `deede6fb5025a247` | 0 |

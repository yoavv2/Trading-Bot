# Signals study: signals-v2-wide-2026-11-19

Status: `completed`. Protocol SHA-256 `e48e17b71f348f325c6eeb0bbd15adb9aed24a482f4d085a77f2e717c743c70b`. Code `e4c1b3c9332d9a40d53f7be59cdb435b746ee5b2`.

## Setup

- Assets: MMM, AXP, T, BA, CAT, CVX, CSCO, KO, DD, XOM, GE, GS, HD, INTC, IBM, JNJ, JPM, MCD, MRK, MSFT, NKE, PFE, PG, TRV, UNH, RTX, V, VZ, WMT, DIS, SPY, IWM, DIA, XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, XLY (tiingo, adjusted bars).
- Development: 2015-01-02 to 2022-10-06.
- Validation: 2022-10-07 to 2024-10-04.
- Holdout: 2024-10-07 to 2026-10-07.
- Costs: 5 bps slippage on each fill, 1 USD per order; capital 100000, fractional quantity, one asset per run.
- Study `59489ef0-fec9-4c26-b9aa-59f99168e53c`, revision `433039b6-436c-4f3b-ae20-831d4c3aba6f`.

| Version | Version id | Specification SHA-256 |
|---|---|---|
| trend_following_daily | `9153763e-9e6e-4c66-b2e9-5ab913b9520b` | `2a52b507621cbfa8bb4fbc82426b60fd3e608df5810cbab02991256d4ebeb9ba` |
| rsi_mean_reversion_daily | `98348948-47be-4e64-a958-c6db0051aaa7` | `acd119da1ded873328c9d04b2a7819d62d386292c3d5f52c115d851776339cd1` |
| donchian_breakout_daily | `5a4448de-cd95-4d46-b175-ec7709f3bf24` | `aecf590af33d99a358e2c4791c106047872d0a56dd186fe3cb2cfd0b3fbbc94d` |
| time_series_momentum_daily | `a4bbd0b8-b5ea-45b3-b59e-abafd810cb0f` | `dea864251cc077719c7f81f40f727a6f8e590f06664b089b102bcc74e87c9365` |

## Daily engine against the backtest

378 runs checked, 11117 signals and 9543 closed trades compared, **0 differences**. 0 runs not checked.


## Development window, pooled over the assets

| Version | Signals/mo | Trades/mo | Closed | Expired: below stop / above target / reward-risk | Exits: stop / target / rule | Mean net R | Total net R | Mean gross R | Win rate | Max R drawdown | Worst asset equity drawdown | Flagged trades | Total net R if target first |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| trend_following_daily | 31.47 | 29.18 | 2714 | 3 / 1 / 209 | 333 / 405 / 1976 | 0.045 | 122.19 | 0.067 | 31.2% | 78.91 | -41.6% | 0 (0.0%) | 122.19 |
| rsi_mean_reversion_daily | 11.34 | 7.42 | 665 | 52 / 5 / 308 | 416 / 206 / 43 | 0.045 | 30.23 | 0.076 | 37.4% | 137.22 | -56.5% | 1 (0.2%) | 34.55 |
| donchian_breakout_daily | 12.20 | 11.31 | 1053 | 4 / 1 / 78 | 384 / 258 / 411 | 0.020 | 20.83 | 0.042 | 36.9% | 73.36 | -45.9% | 0 (0.0%) | 20.83 |
| time_series_momentum_daily | 31.80 | 26.98 | 2502 | 51 / 0 / 398 | 1022 / 699 / 781 | 0.103 | 257.13 | 0.127 | 36.7% | 147.34 | -51.4% | 0 (0.0%) | 257.13 |

Total net R per asset:

| Version | MMM | AXP | T | BA | CAT | CVX | CSCO | KO | DD | XOM | GE | GS | HD | INTC | IBM | JNJ | JPM | MCD | MRK | MSFT | NKE | PFE | PG | TRV | UNH | RTX | V | VZ | WMT | DIS | SPY | IWM | DIA | XLB | XLE | XLF | XLI | XLK | XLP | XLU | XLV | XLY |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| trend_following_daily | 1.14 | 7.17 | -0.27 | 10.18 | 15.64 | 5.64 | 1.92 | -3.45 | -3.09 | 11.48 | -5.78 | 13.31 | 12.40 | 0.80 | -8.73 | -0.27 | 13.30 | 9.60 | -6.38 | 9.87 | 3.38 | 0.70 | 2.31 | 2.09 | 4.53 | 5.27 | 0.89 | -4.20 | -5.55 | -1.08 | 2.26 | -1.05 | 2.55 | -0.91 | 6.68 | 5.53 | 0.31 | 6.97 | 0.74 | 2.04 | -0.00 | 4.25 |
| rsi_mean_reversion_daily | 2.91 | 3.75 | 1.01 | 3.63 | 11.30 | -12.67 | 4.97 | 1.80 | -12.68 | -14.69 | -12.57 | -5.56 | -0.57 | -3.28 | 3.65 | 5.03 | 2.20 | -1.79 | 10.19 | 5.44 | -2.01 | 10.30 | 0.04 | -0.88 | 15.55 | -7.03 | -1.89 | 3.59 | 9.80 | -5.68 | 0.42 | 3.56 | 5.70 | -2.82 | -6.98 | 1.30 | -6.83 | -1.72 | 1.82 | 8.25 | 8.55 | 5.10 |
| donchian_breakout_daily | -4.66 | 3.82 | -6.00 | 8.02 | 0.33 | 5.16 | -3.75 | -2.90 | 0.24 | -6.45 | -0.45 | 3.56 | 3.59 | -9.65 | -7.69 | 0.36 | 2.54 | 12.48 | -12.32 | 16.73 | -1.89 | 4.15 | 5.56 | -6.04 | 5.08 | -2.98 | 0.53 | -6.16 | 5.50 | -0.96 | 3.64 | -6.06 | 6.34 | 2.76 | -3.80 | -0.70 | -2.52 | 8.37 | -3.67 | 2.04 | -0.66 | 9.33 |
| time_series_momentum_daily | -0.84 | -0.64 | -7.11 | 7.77 | 12.40 | 9.39 | 6.69 | -2.55 | 0.81 | 10.10 | -3.13 | -2.85 | 21.34 | -6.78 | -7.56 | 6.09 | -3.97 | 18.29 | -3.46 | 43.47 | 9.50 | 8.59 | 11.00 | 2.31 | 37.75 | 6.29 | 19.22 | -3.15 | 6.11 | -6.55 | 7.03 | 2.34 | 6.38 | 6.18 | 2.36 | 1.66 | 1.54 | 15.88 | 5.91 | -0.49 | 10.76 | 9.05 |

## Validation window, pooled over the assets

| Version | Signals/mo | Trades/mo | Closed | Expired: below stop / above target / reward-risk | Exits: stop / target / rule | Mean net R | Total net R | Mean gross R | Win rate | Max R drawdown | Worst asset equity drawdown | Flagged trades | Total net R if target first |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| trend_following_daily | 30.52 | 28.68 | 660 | 2 / 0 / 42 | 77 / 117 / 466 | 0.074 | 48.93 | 0.095 | 30.0% | 32.82 | -38.4% | 0 (0.0%) | 48.93 |
| rsi_mean_reversion_daily | 6.39 | 5.47 | 129 | 1 / 0 / 21 | 63 / 52 / 14 | 0.509 | 65.68 | 0.535 | 51.2% | 24.71 | -26.8% | 0 (0.0%) | 65.68 |
| donchian_breakout_daily | 12.98 | 12.53 | 277 | 0 / 0 / 11 | 94 / 91 / 92 | 0.260 | 72.01 | 0.281 | 43.7% | 19.57 | -19.7% | 0 (0.0%) | 72.01 |
| time_series_momentum_daily | 32.32 | 30.10 | 685 | 2 / 0 / 51 | 189 / 179 / 317 | 0.224 | 153.53 | 0.246 | 42.5% | 41.00 | -47.7% | 0 (0.0%) | 153.53 |

Total net R per asset:

| Version | MMM | AXP | T | BA | CAT | CVX | CSCO | KO | DD | XOM | GE | GS | HD | INTC | IBM | JNJ | JPM | MCD | MRK | MSFT | NKE | PFE | PG | TRV | UNH | RTX | V | VZ | WMT | DIS | SPY | IWM | DIA | XLB | XLE | XLF | XLI | XLK | XLP | XLU | XLV | XLY |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| trend_following_daily | 6.19 | 4.59 | 3.76 | -3.46 | 7.21 | -7.15 | -0.41 | 0.84 | -0.73 | -5.77 | 18.67 | -1.50 | 1.02 | 0.04 | 8.09 | -1.14 | 7.11 | -1.27 | 0.95 | 7.05 | -3.75 | -3.24 | -1.56 | 0.39 | -1.38 | 1.69 | 2.43 | 0.18 | 3.86 | 3.64 | 2.63 | -3.47 | 0.54 | -3.93 | -4.96 | 0.73 | 1.24 | 8.57 | -0.60 | 2.70 | -0.61 | -0.27 |
| rsi_mean_reversion_daily | -1.39 | 3.33 | 3.55 | -0.62 | -0.00 | 1.52 | 2.12 | 2.26 | 3.23 | 7.17 | 0.00 | 3.66 | -1.28 | 0.87 | 3.46 | -0.90 | 1.68 | 0.79 | 2.09 | -1.36 | -2.83 | -0.27 | 7.90 | 4.06 | -0.49 | -1.28 | 1.83 | 2.10 | 0.00 | 3.14 | 1.42 | 4.61 | 1.16 | 0.00 | 5.29 | -0.59 | -1.02 | 1.58 | 0.18 | 1.56 | 5.28 | 1.86 |
| donchian_breakout_daily | -1.55 | 3.28 | 4.38 | -0.71 | 9.35 | 0.37 | -0.06 | 1.01 | -0.81 | 1.95 | 16.83 | 4.24 | 4.56 | -3.61 | 5.07 | -0.61 | 2.64 | 2.34 | 3.43 | 1.14 | -3.09 | -1.20 | -0.77 | 5.76 | -1.28 | 1.91 | -1.73 | -0.45 | 4.21 | 3.24 | 2.39 | -1.33 | 1.70 | -2.32 | 3.81 | 0.79 | 1.95 | 4.36 | 1.45 | 1.17 | -0.29 | -1.53 |
| time_series_momentum_daily | 7.48 | 10.22 | 3.28 | 1.62 | 17.61 | -0.57 | 3.04 | 2.80 | 0.43 | 1.46 | 23.79 | 3.91 | 2.24 | -3.93 | 8.81 | -4.30 | 7.03 | 0.93 | 5.32 | 7.24 | -2.17 | 3.14 | 2.83 | 2.96 | -7.83 | 1.47 | 4.80 | 6.35 | 10.20 | -4.10 | 6.48 | 0.75 | 3.07 | 0.19 | 3.29 | 4.05 | 6.53 | 6.62 | 0.53 | 2.10 | -0.17 | 4.02 |

## Holdout window, pooled over the assets

| Version | Signals/mo | Trades/mo | Closed | Expired: below stop / above target / reward-risk | Exits: stop / target / rule | Mean net R | Total net R | Mean gross R | Win rate | Max R drawdown | Worst asset equity drawdown | Flagged trades | Total net R if target first |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| time_series_momentum_daily | 44.26 | 36.97 | 858 | 7 / 2 / 166 | 405 / 281 / 172 | 0.224 | 192.32 | 0.261 | 38.9% | 47.53 | -57.3% | 0 (0.0%) | 192.32 |

Total net R per asset:

| Version | MMM | AXP | T | BA | CAT | CVX | CSCO | KO | DD | XOM | GE | GS | HD | INTC | IBM | JNJ | JPM | MCD | MRK | MSFT | NKE | PFE | PG | TRV | UNH | RTX | V | VZ | WMT | DIS | SPY | IWM | DIA | XLB | XLE | XLF | XLI | XLK | XLP | XLU | XLV | XLY |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| time_series_momentum_daily | 3.40 | 3.27 | 0.86 | 3.80 | 8.38 | 5.88 | 19.12 | 4.62 | -2.82 | 3.92 | 15.85 | 12.36 | -2.93 | 30.54 | -13.03 | 11.13 | 13.65 | -1.15 | 3.21 | -2.81 | 0.17 | -0.65 | -3.48 | 9.73 | -6.67 | 10.43 | 10.56 | 4.80 | 13.12 | -1.82 | 5.56 | 4.59 | 3.54 | -0.38 | 4.80 | 9.77 | 3.53 | 4.69 | 2.49 | 1.86 | 0.86 | -2.44 |

## Selection

Verdict: `candidate_selected`.

| Version | Qualifies | Ranking value | Failed criteria |
|---|---|---|---|
| time_series_momentum_daily | yes | 0.103 | none |
| trend_following_daily | yes | 0.045 | none |
| rsi_mean_reversion_daily | no | 0.045 | development_net_r_below_max_drawdown, development_too_few_profitable_assets |
| donchian_breakout_daily | no | 0.020 | development_net_r_below_max_drawdown, development_too_few_profitable_assets |

## Holdout

Version run on the holdout: `time_series_momentum_daily`. Verdict: `confirmed`.
Pilot candidate: `time_series_momentum_daily`.

## Runs

| Version | Asset | Window | Run id | Input digest | Results digest | Engine differences |
|---|---|---|---|---|---|---|
| trend_following_daily | MMM | development | `a9bca6fa-cc94-4104-b7e9-68a61cf1702c` | `3bed235919e4da6a` | `5a21367f631919de` | 0 |
| trend_following_daily | MMM | validation | `21df21b6-99c9-4da2-bde4-2626812a9b88` | `de8668e0af7e4534` | `7fe579b49c492c7a` | 0 |
| trend_following_daily | AXP | development | `7eff9127-f55c-48c5-9a0a-ad463759a2fb` | `ae368a96fa202f0a` | `686d00c3c0b35d22` | 0 |
| trend_following_daily | AXP | validation | `6a9100c1-fca4-4753-80df-3d7ebd636050` | `9bdc09abad09c7d1` | `142faaaf3d760d4f` | 0 |
| trend_following_daily | T | development | `dd6facf6-c1f7-49f9-81a5-234e3c29900c` | `570c68fe851214eb` | `ef0f400d637b1683` | 0 |
| trend_following_daily | T | validation | `3bf761df-7132-4332-9f08-6ddf440cdd46` | `2a04aa6ede17bdf2` | `afd72e0b66450d75` | 0 |
| trend_following_daily | BA | development | `6ac9d11a-1759-4e56-8c9f-187fe59fd5aa` | `0c3d65bf161a0e4c` | `3f86b4b5fd940d9f` | 0 |
| trend_following_daily | BA | validation | `0ee182ba-c1b9-4e0d-81ab-199253475b80` | `dbc6e9e9c8eec0ca` | `460a8bcc30b1864d` | 0 |
| trend_following_daily | CAT | development | `7d2d8efd-6873-41d0-bf22-411a34788c3f` | `68b80cfbe5cdc929` | `3cf47c1496e101f5` | 0 |
| trend_following_daily | CAT | validation | `3af16b95-5392-415b-9664-4fbe7a09b124` | `45568b957eace472` | `1e1a7caef0e8d8e9` | 0 |
| trend_following_daily | CVX | development | `0cb70105-042e-4c07-b898-660e0406f392` | `d887a8ec7a5c4e27` | `87f5020ce54b2d1e` | 0 |
| trend_following_daily | CVX | validation | `a0622317-78a8-4e8b-9e5e-abaa68df0d6d` | `e1d39438f086d78b` | `96cb3ac719b52129` | 0 |
| trend_following_daily | CSCO | development | `c5501e60-c9ed-4229-bf21-b9dbb1cab022` | `3add91468a6e4319` | `f2c27f857f86900f` | 0 |
| trend_following_daily | CSCO | validation | `79a14384-ddbf-4311-8233-121a4f558f86` | `9d421a5c40e08d2f` | `2138e91a50a7561f` | 0 |
| trend_following_daily | KO | development | `6f1b873e-d951-4c3b-aec1-8bbb518ecb5e` | `a46cdb6c314e28ac` | `f4b79592fcf0a660` | 0 |
| trend_following_daily | KO | validation | `91fce2ae-86c6-4f85-a4cb-63284e8d07cf` | `0190a288318956d7` | `c8c7e61424ae2537` | 0 |
| trend_following_daily | DD | development | `800dcb5b-be67-4d60-b0ad-3d21f3b50a6d` | `b253e7e774daae1e` | `f71a5715e2dd6bcc` | 0 |
| trend_following_daily | DD | validation | `6e394bc8-a90d-4a19-ac32-f7b8fde49c9c` | `f2abab262d7f74d6` | `aed96d9ad228ee39` | 0 |
| trend_following_daily | XOM | development | `e573dad7-1263-4c48-9d0b-ff7bd95c0b11` | `36882c8efb4ff72e` | `df34eb226e63159f` | 0 |
| trend_following_daily | XOM | validation | `e82efe3f-fdda-4653-b329-b0c6c84a3631` | `4c6ce7bc43b243fd` | `932e98a88f570164` | 0 |
| trend_following_daily | GE | development | `20709e34-d238-49ae-9019-9033d680199a` | `433a5b000cfacac8` | `71e435d78fa25399` | 0 |
| trend_following_daily | GE | validation | `7c0e8448-4560-4209-89d5-741f979cb863` | `f733b0e034cd9f2a` | `4e97e6d4dd22f75c` | 0 |
| trend_following_daily | GS | development | `bc0c69d5-d28d-4ccf-b7c6-fc04603c6f66` | `80d0f5b02ac73826` | `fcc47f60241ff532` | 0 |
| trend_following_daily | GS | validation | `8c0bbe70-1fa2-4d6b-82ee-1cddd7eecd81` | `6559c1a33a558a32` | `be14f371c1f3d8cf` | 0 |
| trend_following_daily | HD | development | `f00768c8-cece-4f26-9519-35759c6aa599` | `70c531eb319f3e0d` | `278e376d30c6ee9b` | 0 |
| trend_following_daily | HD | validation | `46c122e4-8f45-4c2a-9a89-5cd4ea8a0cff` | `b11b16a5dab8cd4a` | `66775f38b3c4cad6` | 0 |
| trend_following_daily | INTC | development | `dfea760d-8234-44a8-bd6d-2f5f24c0d84b` | `d03c5ce8c04946b9` | `908143764bfa6a30` | 0 |
| trend_following_daily | INTC | validation | `ecf50f16-2719-4111-bd7c-13c80b4d4e0d` | `155e08222cf15a19` | `a25d8a7659c07446` | 0 |
| trend_following_daily | IBM | development | `12deceb8-b370-4ea1-bfcd-d9c929464565` | `f18d287eb5634caf` | `be07e27b99c4218f` | 0 |
| trend_following_daily | IBM | validation | `217cedbe-0548-430e-b5f1-5a185214f9db` | `3dbb0cf857f4d093` | `62bb57b1598d8feb` | 0 |
| trend_following_daily | JNJ | development | `6ed9a149-a995-47ac-918f-ce62b029b111` | `48f48a358a80f10b` | `dc680085911fd698` | 0 |
| trend_following_daily | JNJ | validation | `41ba7510-da79-4b13-875f-b22194e3d0d9` | `639fbea0f07a4202` | `d98ec6036346aa4c` | 0 |
| trend_following_daily | JPM | development | `30ef060e-480f-4307-948f-a0d72ecd1077` | `b9241ca839dc7091` | `cbfe47c767b4e03a` | 0 |
| trend_following_daily | JPM | validation | `aebb95a7-0897-4c36-92c0-7d7cd8128921` | `ec7124a89c611e18` | `58f3134bdc70d04a` | 0 |
| trend_following_daily | MCD | development | `ba5f09d3-049c-45d8-9c02-137ab099177d` | `fca099a3b01512ce` | `ca381a77fbcda5cc` | 0 |
| trend_following_daily | MCD | validation | `2a25d39a-d094-4be2-8802-ad0d5c0c9ec8` | `4c73337fd6f7c19a` | `ae1549dfd53ce715` | 0 |
| trend_following_daily | MRK | development | `6ee23a08-a640-4221-ac4a-95898f1b9d7d` | `b23c894bcedaceb7` | `9ef7c89ec795ac04` | 0 |
| trend_following_daily | MRK | validation | `3a189627-4b03-4ac4-bb5c-cb3a5cbc55de` | `826d92ee1bb0570b` | `257c643bff0e7e51` | 0 |
| trend_following_daily | MSFT | development | `8855981a-219e-4bc5-8872-0b78f245340b` | `d7ea3a84a163a3f0` | `61d7da25dd00b515` | 0 |
| trend_following_daily | MSFT | validation | `e314e5a6-0637-45fe-b47f-85e2126fd7fc` | `28233e9faf10807f` | `8329b17b5f2e4e87` | 0 |
| trend_following_daily | NKE | development | `da36e777-ac95-4393-9a81-1d25e6294a68` | `2dd22f743eb2712a` | `d556bc81e6a59481` | 0 |
| trend_following_daily | NKE | validation | `fdee9041-bbe8-4ca2-b7a8-d21378504da5` | `0e9f0b90c8917b69` | `34967bda20ed062c` | 0 |
| trend_following_daily | PFE | development | `3671a9c7-50d5-476d-bd9c-3a1328f06cc0` | `516ade1f82ca2017` | `f2c0920f312a12da` | 0 |
| trend_following_daily | PFE | validation | `a8c96506-df6e-4866-9846-f1eb999b6cfa` | `d7b29d80913b3c9a` | `e1778e5331eda9a1` | 0 |
| trend_following_daily | PG | development | `523d2924-9007-4bf1-9fb5-8f626afc55a1` | `2df2e4bbd7e11c5e` | `5a709a1ab429b2cb` | 0 |
| trend_following_daily | PG | validation | `86dc512c-4611-4fde-89ac-81178d0129c1` | `64a32daa4684ca72` | `1adccaf29a7be5e0` | 0 |
| trend_following_daily | TRV | development | `304fb9e1-79ae-4b28-8091-af675cfa975b` | `14f6cd8941467912` | `ef2a2be7172f4a92` | 0 |
| trend_following_daily | TRV | validation | `4efafd34-2303-46ce-b2d3-e460a62cb8e7` | `eb318ce68db6ae2a` | `4fcd7c542bd4a857` | 0 |
| trend_following_daily | UNH | development | `6360c80d-14e0-43a6-9a5d-bb4a61d6bb47` | `2d90d0c734fb92de` | `e1631034dfac8f7b` | 0 |
| trend_following_daily | UNH | validation | `ca6c7af5-7736-44fc-a180-b18b9db9f1ce` | `2eea97b2860f15f8` | `98729dc626a6fadb` | 0 |
| trend_following_daily | RTX | development | `56813d21-bdd3-49e2-9293-2d0776e631bc` | `7dc80bd4a8714cd9` | `96bf3131499cdab2` | 0 |
| trend_following_daily | RTX | validation | `8106dcc7-430b-4e63-9fde-6e958620f4ca` | `c152e87219c66c0c` | `ca17e8ccc059e7e0` | 0 |
| trend_following_daily | V | development | `8b0bb5f8-06a1-4cf7-9f5c-4e714115bb50` | `d3b7d15504c15cd0` | `5a76107812207413` | 0 |
| trend_following_daily | V | validation | `096d9924-5c54-4513-b2b8-1ebdfd960c96` | `9c6c69f4454f14de` | `1a71c129726be93e` | 0 |
| trend_following_daily | VZ | development | `df257aba-0197-46d4-9db5-ef4314fe6678` | `0cc5f0be85a06acf` | `9b2c48254c5022ef` | 0 |
| trend_following_daily | VZ | validation | `b05bb031-3113-4bac-baf4-ba9bc907df45` | `35638a3ec57c2519` | `6d444b2cf8767ae0` | 0 |
| trend_following_daily | WMT | development | `3ef70550-30ab-4294-a54e-213f9c9f9fe2` | `dc9c10c26c957034` | `fb78cabd9c9488c8` | 0 |
| trend_following_daily | WMT | validation | `625227df-dae1-495a-9c98-86f93ca80cbc` | `1add031dda897f72` | `92a09301c77e55e9` | 0 |
| trend_following_daily | DIS | development | `35a3686a-d283-4a90-9620-698659b43f34` | `9100c0735ed0707e` | `9b6e0a37a3a08da8` | 0 |
| trend_following_daily | DIS | validation | `6c01efd6-5016-42f0-bba5-232c60fe8693` | `289267159d9cff6b` | `a704038876eb8b84` | 0 |
| trend_following_daily | SPY | development | `9db7d371-964a-4d25-87a7-1cb0eecf103f` | `84450ec94e595c32` | `d8c6768e0054ac5d` | 0 |
| trend_following_daily | SPY | validation | `0a571dde-6235-4125-846a-193fb0733ca3` | `7adc4283b2379120` | `98a308d7c5ea4eb4` | 0 |
| trend_following_daily | IWM | development | `07e979a7-1780-4885-b7c9-2b3448500034` | `0108d5f6d52ebe4e` | `8239939dd690bc7b` | 0 |
| trend_following_daily | IWM | validation | `02f8c130-be99-40f4-8739-ea93c9e84846` | `d416cfabf0c5555c` | `08895d8010479d6e` | 0 |
| trend_following_daily | DIA | development | `d6a7e9a6-9a3c-4c0e-8aed-82be08fb348a` | `711600f5a567dd65` | `eb153064f1679d8c` | 0 |
| trend_following_daily | DIA | validation | `5d00f07c-2912-447d-a6f3-d8818d92d0b0` | `24633cc8e1e9f40c` | `55c7dc11d8d83360` | 0 |
| trend_following_daily | XLB | development | `8f2325fd-4697-49ae-8d73-7628e56738f7` | `4820f652454c8f88` | `915c403d2b3b99f0` | 0 |
| trend_following_daily | XLB | validation | `cc7b8a6e-907c-4923-9d25-dd18a770a35e` | `6f9fed08039f5fd4` | `533abb511aa4cfef` | 0 |
| trend_following_daily | XLE | development | `23f516a7-8f16-410d-b01a-67191946282b` | `7d7b2bac64be304a` | `84ed0ef0ef09c364` | 0 |
| trend_following_daily | XLE | validation | `0be1443f-ac6b-4f19-b52b-ce923384c8d8` | `4022cc69d1514dfd` | `54139ec5e40220aa` | 0 |
| trend_following_daily | XLF | development | `2cf2bc85-f5b5-43f2-89aa-9569ac04e2b7` | `bf7dac0344719c42` | `c414110644940dbe` | 0 |
| trend_following_daily | XLF | validation | `2ebb096a-87f8-484c-b3d9-9039617234d2` | `ddc0e449796b6c3b` | `bce4eb29c18e685f` | 0 |
| trend_following_daily | XLI | development | `18546978-e507-4350-ab6a-98f29a798568` | `6aa4024e8725cda1` | `6088e5eb442279e6` | 0 |
| trend_following_daily | XLI | validation | `01224f60-3820-428d-a13a-2da59945c0e3` | `a7123063f40eb6f5` | `6801686093516bc9` | 0 |
| trend_following_daily | XLK | development | `b8115246-ab7f-4095-b98f-560c287c41f7` | `85c14784c132255a` | `ab76fce108f7aac8` | 0 |
| trend_following_daily | XLK | validation | `37e8be6c-dd2c-4834-b75d-eab93c60e3b9` | `8f8c0c8599fc0915` | `3522b8082ee04ee9` | 0 |
| trend_following_daily | XLP | development | `831e39f6-27a1-4e6b-82a0-2a8a9e96db55` | `d3ccd2f299573961` | `9a244c4a8cbd48a5` | 0 |
| trend_following_daily | XLP | validation | `7d0a485b-8149-42cf-96d0-bdcd37c374b6` | `4ca45f50b4acaddb` | `7af7657c83de3df8` | 0 |
| trend_following_daily | XLU | development | `103bc4fc-e5a0-4e58-a188-ab3c5692aaf0` | `f8a311328eba2c4d` | `40a5a18d16b9550b` | 0 |
| trend_following_daily | XLU | validation | `31d51ace-d888-41d6-be4b-43eca7a2da79` | `b2e43b13933b141a` | `69cb6579b27ffd4f` | 0 |
| trend_following_daily | XLV | development | `d32ba1e3-52ee-45b3-8a85-c7b199efadf2` | `a8cbaa75bf3c6ccc` | `c0882ca63ce2a317` | 0 |
| trend_following_daily | XLV | validation | `046742ab-d811-49be-ba9a-c44b6fe0e642` | `ae0d64e38e9114ce` | `685d21eb2c7ed942` | 0 |
| trend_following_daily | XLY | development | `592070a4-d012-47fe-8a9d-7de4ded83c1c` | `05bd4bcc0c086c47` | `b48108acd52189ad` | 0 |
| trend_following_daily | XLY | validation | `7223a860-9d88-4f9d-8032-93572c4f0604` | `1a3fd2f436126ba3` | `8e1605ba1c666040` | 0 |
| rsi_mean_reversion_daily | MMM | development | `9f34ba32-8a97-415c-87d9-1e1b16310f10` | `ebd3378a8d3d655b` | `05a5588f218d6b8f` | 0 |
| rsi_mean_reversion_daily | MMM | validation | `ea73d718-d02c-49e2-9020-79e426bbf61e` | `5c91d4d36118b96b` | `d27cc61c7d450cbf` | 0 |
| rsi_mean_reversion_daily | AXP | development | `8cbfc1ab-0ffc-4c98-8001-950475eebb26` | `7688444ead9ab5f7` | `8d517d16ba7be2f0` | 0 |
| rsi_mean_reversion_daily | AXP | validation | `cb33aaa7-be38-4cec-bb7d-24a4b8929c75` | `97aef34663809b91` | `b09e9f401d33a1d9` | 0 |
| rsi_mean_reversion_daily | T | development | `6bfa762e-99f0-4390-8c06-74083d69d4ff` | `fdee3c9713856daf` | `0b888b988db11a5e` | 0 |
| rsi_mean_reversion_daily | T | validation | `0e50e5ba-1766-47e7-bddd-9f64217788e2` | `137426046d452039` | `20d97c6438a0b465` | 0 |
| rsi_mean_reversion_daily | BA | development | `4f5a016c-7033-414e-aa42-b01816eb9ca5` | `9d84e99dc06a9bda` | `39abb77d817038bd` | 0 |
| rsi_mean_reversion_daily | BA | validation | `4f98bc8a-c8d3-4f8f-ac85-51a415c604e5` | `180966518ca046d5` | `05372830299dca6a` | 0 |
| rsi_mean_reversion_daily | CAT | development | `c036a615-bdd3-499c-9745-1fb13d654bfc` | `00930ca05898d165` | `8a2af300a4300458` | 0 |
| rsi_mean_reversion_daily | CAT | validation | `066af0b9-8558-49ea-a5ed-cc5402630b8f` | `b1ce389d90fe6da5` | `c2d99548ac4f3575` | 0 |
| rsi_mean_reversion_daily | CVX | development | `e777f9ff-b229-49fc-bc61-150c147b32e9` | `82fe8849b62adc23` | `ffb8f1a12a160652` | 0 |
| rsi_mean_reversion_daily | CVX | validation | `171b7508-2cc8-4c73-a9c2-e564ae515039` | `30e7dae5696e03e4` | `b5b1af9077b1d914` | 0 |
| rsi_mean_reversion_daily | CSCO | development | `19212737-fab6-420f-990e-25825c6ecebc` | `412925b73ca6c0e3` | `c734675d7d2c923c` | 0 |
| rsi_mean_reversion_daily | CSCO | validation | `cc9eed8c-b473-47ed-9021-e373e5c1a469` | `4f61a82a351d0e73` | `5c5a8e6069d5b2d3` | 0 |
| rsi_mean_reversion_daily | KO | development | `38d48541-04fa-4d2e-ba8a-23b319cc715a` | `2089f45d0b478ec7` | `e0553d34078cedb1` | 0 |
| rsi_mean_reversion_daily | KO | validation | `c2c2f923-af93-4573-b606-2c0bb728813d` | `cbe53ea29563c41d` | `fa1d99b266b684ca` | 0 |
| rsi_mean_reversion_daily | DD | development | `547bee49-5600-4544-abcb-16ea386045d9` | `0f98bc2471dd3682` | `c0f7b89cbf1f62d5` | 0 |
| rsi_mean_reversion_daily | DD | validation | `2e6ab8d3-07be-4b1b-9fdc-d5cf36b87d98` | `0e8204accd11914e` | `962a691151b6cf54` | 0 |
| rsi_mean_reversion_daily | XOM | development | `5f30c95e-c162-448e-9be5-5067857da589` | `cd5d2b1a979cdfa5` | `afb7bf52626bd884` | 0 |
| rsi_mean_reversion_daily | XOM | validation | `1fe44d28-e228-4708-8e91-387f96b4874a` | `4b60e5fa71923dc6` | `74a387775410dce0` | 0 |
| rsi_mean_reversion_daily | GE | development | `1c2631ae-21b3-43de-be01-42705c4c478f` | `9c76aa802b314423` | `04716ce26a3f18fc` | 0 |
| rsi_mean_reversion_daily | GE | validation | `72f524a3-c677-4e73-919a-5261df224e07` | `d30578a0a71c6935` | `d8826f04591e1010` | 0 |
| rsi_mean_reversion_daily | GS | development | `7c8e1d41-d703-4ea4-8577-c1e99107a35c` | `3fbad96f3976dff0` | `f5e22b72a738a5b7` | 0 |
| rsi_mean_reversion_daily | GS | validation | `29d2e484-60f0-45ca-93b1-0e3bdaa3c570` | `23fcf354070e9203` | `40f9ef34ec66d2f7` | 0 |
| rsi_mean_reversion_daily | HD | development | `c9d91bf1-05bd-4b62-8540-9bda99355695` | `b870a6dd3682ca36` | `f27a1b1d58efa5d8` | 0 |
| rsi_mean_reversion_daily | HD | validation | `c0946277-20d8-4655-a140-9128690abdf3` | `6e29f72159c48115` | `849e2d5881ab0eef` | 0 |
| rsi_mean_reversion_daily | INTC | development | `b5432a59-aebb-4ee4-9a21-908443f02700` | `932bbf267f126da7` | `ca763f0c40ae711c` | 0 |
| rsi_mean_reversion_daily | INTC | validation | `5d3dc4ef-3b54-429f-b571-ba7245c40a79` | `bcf0f2b202fdcd38` | `0ca571d5be726594` | 0 |
| rsi_mean_reversion_daily | IBM | development | `f7b5d5a3-5f82-43be-bd28-f64f483c6ec2` | `26bbf52d6e8dd9ca` | `a84416513d20bcd5` | 0 |
| rsi_mean_reversion_daily | IBM | validation | `b70d0281-851b-46dd-ab0c-3495ff1d9ee9` | `c399b127f20e13d7` | `9ae67c0f53a47695` | 0 |
| rsi_mean_reversion_daily | JNJ | development | `d89b2bfc-bcd9-4527-8b46-ccad6d9a0bf4` | `283881483c539796` | `abcc8779b5dca3ae` | 0 |
| rsi_mean_reversion_daily | JNJ | validation | `fcd04fbf-04ff-43db-a44f-ccbd3f33215d` | `dab58072ab6f5c84` | `1be81a566dde68f2` | 0 |
| rsi_mean_reversion_daily | JPM | development | `0ea3cf0e-db09-4e76-b7ea-8cc938259688` | `4642aaf45b100eef` | `ff339ba046c95174` | 0 |
| rsi_mean_reversion_daily | JPM | validation | `ff591d77-13f2-45c7-a2a6-579f35d2cffd` | `e2e2433e387e5d46` | `8eab37f336ee7fa3` | 0 |
| rsi_mean_reversion_daily | MCD | development | `a89f26fe-7208-4b60-af9f-a4d3d7fb452b` | `2eddd1b1bd2b9047` | `fa11d17b31a1f645` | 0 |
| rsi_mean_reversion_daily | MCD | validation | `c9b963ad-16e9-4ca2-9907-f87b642b991a` | `cafa1823de31d618` | `ea34ace7e5320a76` | 0 |
| rsi_mean_reversion_daily | MRK | development | `67b57797-6c87-45b7-aabf-9aef2758a779` | `61e3fe53e8dca8a4` | `9374b2284af2cfee` | 0 |
| rsi_mean_reversion_daily | MRK | validation | `d371aba3-c97a-4374-82c4-691823d443ef` | `1a1c18add07177fe` | `ffa4f35ed94d575e` | 0 |
| rsi_mean_reversion_daily | MSFT | development | `9c33f7bc-211f-4ebe-b1ca-d1764fd5926e` | `ef3aadb302f9dcf2` | `90cfc4fcc2ab652e` | 0 |
| rsi_mean_reversion_daily | MSFT | validation | `f02237b2-b939-40e8-a4e2-65e14e8f333e` | `9aaa6298c949c687` | `ff0d06766a0b3d4c` | 0 |
| rsi_mean_reversion_daily | NKE | development | `9f0e5b53-3fda-45cc-a63d-cef3c451220a` | `e547b4bd0b9386b7` | `c41a6b52a6fcfede` | 0 |
| rsi_mean_reversion_daily | NKE | validation | `7b933844-1373-4a0b-9ca7-38cacb12686d` | `bb6f29ad6cb6e496` | `efaaac8bbdd4826e` | 0 |
| rsi_mean_reversion_daily | PFE | development | `b56cb316-b647-4ec3-a18b-b989f3d75f79` | `cdb62daa7eb1e27e` | `3bbab77156cb76ae` | 0 |
| rsi_mean_reversion_daily | PFE | validation | `5ba4cd63-771a-403f-9a48-3e7e3bcaf583` | `8d148bd7129d40ba` | `80d2f1e8c3524dde` | 0 |
| rsi_mean_reversion_daily | PG | development | `d9c8ace3-fdf7-42b3-ae9c-af2e2469a3b0` | `6e954f7f340105a6` | `53836efdbdb7165c` | 0 |
| rsi_mean_reversion_daily | PG | validation | `cb7dc711-8d95-4ea1-b4b6-a18a222a587a` | `b7c21afd699812b4` | `f7967f653c9cbf31` | 0 |
| rsi_mean_reversion_daily | TRV | development | `40c35ad5-895e-4e3e-b4aa-789b3c634766` | `a33684374ad81a50` | `f877ef46da0bba1e` | 0 |
| rsi_mean_reversion_daily | TRV | validation | `f65f8175-77bf-4bc8-adee-76d17acbc278` | `1cbd5b859927e1a2` | `3dd527b155a6cddd` | 0 |
| rsi_mean_reversion_daily | UNH | development | `917f9ffc-9491-4859-aec1-5dc7bd238a37` | `1e90ddff1e1f1803` | `70177cf55863cf2a` | 0 |
| rsi_mean_reversion_daily | UNH | validation | `d51be7a7-887e-4296-a7f8-e9d37c97b82f` | `fbcf9d01221bf7a4` | `b1385ba1633abf69` | 0 |
| rsi_mean_reversion_daily | RTX | development | `c0d193eb-7b6f-44c6-9630-12958c57643e` | `03abf20ec98b4857` | `e6085b78d2a7876c` | 0 |
| rsi_mean_reversion_daily | RTX | validation | `e8efbb70-2bc4-47cc-8a50-a70f8e09731e` | `527c5a85e133a69d` | `caabeb4fab134cd8` | 0 |
| rsi_mean_reversion_daily | V | development | `f074b613-7fe7-42a5-9673-d1751c6069cb` | `c07d34552e303562` | `23f67379134d9964` | 0 |
| rsi_mean_reversion_daily | V | validation | `3005a776-ac63-447f-b0b6-d908842047f8` | `b7b44b1f482fff28` | `9ded0dcc94ae7b60` | 0 |
| rsi_mean_reversion_daily | VZ | development | `9bfd2e42-1800-431e-9486-1d1b6b93167b` | `0c82b7baeab3294c` | `5eab564f606790fd` | 0 |
| rsi_mean_reversion_daily | VZ | validation | `ef9b498b-7659-475d-8abe-85d96d1b2f4e` | `41f7eb674ae7ed4a` | `53dd4c47ee09c1ab` | 0 |
| rsi_mean_reversion_daily | WMT | development | `1e229526-206f-495e-a904-3b620e89aeb9` | `f71b87f523bd2ea6` | `916f6253e0cbf01a` | 0 |
| rsi_mean_reversion_daily | WMT | validation | `6be100d2-082d-42c1-b166-1caf2fb4a519` | `62f54641666c3d29` | `d8826f04591e1010` | 0 |
| rsi_mean_reversion_daily | DIS | development | `ca883631-ef86-4954-adaf-f5baf498e221` | `b06dfad989139d77` | `1af82655e05ffb28` | 0 |
| rsi_mean_reversion_daily | DIS | validation | `99960c96-9733-4ba9-9dd2-71dcdf119465` | `8ba0d9d5cc8a36a0` | `09d2b76b81437c91` | 0 |
| rsi_mean_reversion_daily | SPY | development | `843f7363-cf3e-4a42-97dd-2cd0e0eadcc7` | `6ad9321726528cd0` | `97d630c2ccf70db4` | 0 |
| rsi_mean_reversion_daily | SPY | validation | `fab4aa60-154b-4fed-a2c2-1d11960ce12b` | `8c1b3feae199b828` | `5bea46d8c16ae472` | 0 |
| rsi_mean_reversion_daily | IWM | development | `eeb3977d-4431-425f-b2be-078e70616b3d` | `db00feb085e4c5a9` | `67d28eef9cdfadb2` | 0 |
| rsi_mean_reversion_daily | IWM | validation | `679ee136-7af2-44b5-9862-c40a8936cc52` | `33670c017f2dde6a` | `6a25d44232c70cdd` | 0 |
| rsi_mean_reversion_daily | DIA | development | `3029fb22-ae74-4fe0-814e-19f0ef2a4afb` | `89cd2e7aed848aec` | `9c7b43d19354c6d4` | 0 |
| rsi_mean_reversion_daily | DIA | validation | `43e172d9-2201-4b33-bf36-27b58ad2a704` | `a5f29d705adb04d6` | `f5738d118dc265fc` | 0 |
| rsi_mean_reversion_daily | XLB | development | `129bd109-abe8-4904-8c6f-d99852be7fce` | `2db8a4f2aa2d2b12` | `2a57f6e9d4e0c1ad` | 0 |
| rsi_mean_reversion_daily | XLB | validation | `cb3c8722-479a-484e-8cc5-c09627b08019` | `e7f4e868f1be8b67` | `d8826f04591e1010` | 0 |
| rsi_mean_reversion_daily | XLE | development | `6eb46a25-a589-4d3b-bfe0-0c6f805a3136` | `ff8af08470b91680` | `62380357d9ad1464` | 0 |
| rsi_mean_reversion_daily | XLE | validation | `92a7baee-a4a0-4fae-93ba-0e5499fb23e2` | `c2108d495cfcc5c4` | `e6d9d3e08b944302` | 0 |
| rsi_mean_reversion_daily | XLF | development | `1e7dc66f-fb22-437a-81cc-91248e066362` | `4277de2e6e8f2aa6` | `4e2f5b7e60d8d837` | 0 |
| rsi_mean_reversion_daily | XLF | validation | `cc5d9e33-b386-4202-9ab7-fe77aa8d50be` | `c001fac073078869` | `6973424908a61c3d` | 0 |
| rsi_mean_reversion_daily | XLI | development | `e412f7f8-604e-4ccb-ab45-11b0af5ceb4a` | `fe5a1101309c5ab9` | `9d45b5a2d3aafd44` | 0 |
| rsi_mean_reversion_daily | XLI | validation | `beac081a-9f73-4152-ad1e-e4972a71991e` | `b47a90cdc3c3a672` | `bcf1dcada04fadab` | 0 |
| rsi_mean_reversion_daily | XLK | development | `ecaa65f2-ed2e-4f47-b776-cd115d1438e2` | `7fd39c1c0d6257e7` | `1219b30b3b0c63ec` | 0 |
| rsi_mean_reversion_daily | XLK | validation | `2f7ca0cc-fd52-4c09-92f4-e4a71aa571ba` | `88396f9f47eae39d` | `604cd45483dcd6c5` | 0 |
| rsi_mean_reversion_daily | XLP | development | `55f462b8-141f-4636-a839-580e6c58d6ef` | `4c774f4f817d1e76` | `ad0768fdd65dac96` | 0 |
| rsi_mean_reversion_daily | XLP | validation | `4aea2f69-9dc8-46fc-9530-c8d1ee9c2975` | `6c88aab931341ad7` | `f04f6251687bd517` | 0 |
| rsi_mean_reversion_daily | XLU | development | `1f6d8abc-80ab-4e32-8e26-5d7887dfa5a1` | `3ee9cacb289fda31` | `457f64414dc28bbd` | 0 |
| rsi_mean_reversion_daily | XLU | validation | `b1a9014d-7eb7-4e5c-94fb-b5cb64babb12` | `b70c203c46e8ed3d` | `b08bcf77c5e6bab6` | 0 |
| rsi_mean_reversion_daily | XLV | development | `8b9710bf-f2d6-4556-8242-17ffd9ae0a2e` | `a0a72e196dffc492` | `f7c9fff1008bba25` | 0 |
| rsi_mean_reversion_daily | XLV | validation | `6d4b1436-51ed-40d4-8e57-82f2d00e5877` | `aa2b56ad2fde721e` | `ca1ca83f57eb51c2` | 0 |
| rsi_mean_reversion_daily | XLY | development | `d163d9e4-ea3b-47a7-bdb3-163400e94c08` | `e9a4327b62c1ebbd` | `0ab3cb6b696bf44c` | 0 |
| rsi_mean_reversion_daily | XLY | validation | `c05fec69-fefa-4a5e-8570-d6026068a95b` | `f4210fa082a51e13` | `0aecc98755ef2aa0` | 0 |
| donchian_breakout_daily | MMM | development | `ca34ead7-57e7-4113-ba88-bb56974525be` | `f513b58a67177bde` | `49b490fdcec58dac` | 0 |
| donchian_breakout_daily | MMM | validation | `4b2a3c83-2743-46aa-91c0-93cc9e860627` | `d9068ed266431135` | `7399a5a691322c42` | 0 |
| donchian_breakout_daily | AXP | development | `1882a98d-6756-4869-85d5-690ddb5a96a3` | `dfb1076cdc09e707` | `a3502c04b5329188` | 0 |
| donchian_breakout_daily | AXP | validation | `57866eea-4cfd-4b03-9892-eaacce64dbdb` | `713b06092234b7a7` | `a25fa3c578e6ac58` | 0 |
| donchian_breakout_daily | T | development | `e79ba66e-a61a-464c-83bd-d116c1422776` | `afbe7caef56171eb` | `39330e67acf9c544` | 0 |
| donchian_breakout_daily | T | validation | `85eaa39b-b396-4de6-bcd6-93f790883a28` | `3427797e17ee4ba8` | `0b0278c2e74445ec` | 0 |
| donchian_breakout_daily | BA | development | `a0ccf485-65cc-4145-8169-d5952e1521e3` | `6039fb8aadce1eac` | `3c0f6c11e74f4dab` | 0 |
| donchian_breakout_daily | BA | validation | `87ee6d34-7cea-4aa2-a1b8-1c6863d8dd3e` | `f1e8c8bf16af901e` | `a1870957b1719237` | 0 |
| donchian_breakout_daily | CAT | development | `60b6de58-5cf4-457d-9765-358934ecdd3d` | `2d3d5bbb13dd2038` | `c1fbe9c49eddda24` | 0 |
| donchian_breakout_daily | CAT | validation | `7ef8adb6-ad73-48d4-ab1b-33272ee602f9` | `57cf958885185c41` | `e211fecc3208a9f9` | 0 |
| donchian_breakout_daily | CVX | development | `b8991781-0bb9-4434-a0a3-897a7212a366` | `dc8531a35025e1dd` | `ede393f0371c7c33` | 0 |
| donchian_breakout_daily | CVX | validation | `b36629b5-b3cc-4c51-93cb-76376f4db964` | `b4d89d57a3728ce9` | `e487d4bf3ee10382` | 0 |
| donchian_breakout_daily | CSCO | development | `666965f7-b8eb-462f-b415-022731a2842d` | `f0c6e6902f568965` | `cf053f2b3258b9fe` | 0 |
| donchian_breakout_daily | CSCO | validation | `bdf32947-3cca-452c-a802-bd80ef82d274` | `555573ea1122648d` | `d32a6145237da2c3` | 0 |
| donchian_breakout_daily | KO | development | `c4b835c1-59a8-498f-aac5-1469c5e87add` | `4946fa9abc8e6a7f` | `8fcf21aa8215010c` | 0 |
| donchian_breakout_daily | KO | validation | `a920751f-1d96-4b8c-8778-97644a2b1944` | `d2d5ff44abba957f` | `c7c9a5679f6ec98b` | 0 |
| donchian_breakout_daily | DD | development | `d0fc58fc-582c-4086-94b5-64e2ec10813f` | `abd5362436277195` | `2d02a17777620ecf` | 0 |
| donchian_breakout_daily | DD | validation | `76c7cdd7-a23d-4b90-ab2b-6760595feef6` | `c17b66b79ecb5e27` | `8215bcc6245785f2` | 0 |
| donchian_breakout_daily | XOM | development | `a1453653-0a27-429b-8573-173ce5d3136c` | `c08659a7d3281447` | `fd94e6424c23f186` | 0 |
| donchian_breakout_daily | XOM | validation | `971053c4-b630-417c-986b-7302fb6a7caa` | `9dcff3756dacbf5c` | `54ee9bae98dfb547` | 0 |
| donchian_breakout_daily | GE | development | `b2f3dbb3-aa09-4cac-a6bd-83dc9b8513f4` | `bc31089e678bf4c3` | `5d2b4e407afe2341` | 0 |
| donchian_breakout_daily | GE | validation | `7726244d-3b79-4bee-afb4-68f92a413e64` | `0a63f38c3e572686` | `97c96870f28f64ce` | 0 |
| donchian_breakout_daily | GS | development | `7a57011b-d415-45a4-af69-55940a1250ae` | `8440e183cee45774` | `740408311521ab37` | 0 |
| donchian_breakout_daily | GS | validation | `418d8010-bb7a-48b4-9343-282fcca1399f` | `a0ccc3b2793316f0` | `ee46e18d7b44b9b3` | 0 |
| donchian_breakout_daily | HD | development | `22d0dc83-0519-4fee-af9a-119188492822` | `f096edf41d1e7e6e` | `d4c305b6909bb626` | 0 |
| donchian_breakout_daily | HD | validation | `95a7fb53-59da-484d-850d-1ac807f60d6c` | `cade9a4f76f761c9` | `9507ef2ed284b8da` | 0 |
| donchian_breakout_daily | INTC | development | `9abd86d5-ab60-4b3b-aba7-14dcf90e4417` | `8824b050264011f1` | `37ef75ff11b932d4` | 0 |
| donchian_breakout_daily | INTC | validation | `d54f29ff-8885-4005-93d9-475476759ef7` | `9f437228e2a2e6f7` | `c1672b75d8b4532f` | 0 |
| donchian_breakout_daily | IBM | development | `505b7353-83b2-42fe-af05-402e9e4a381e` | `93a7ad4193567fe2` | `ed34e603e77d128c` | 0 |
| donchian_breakout_daily | IBM | validation | `30d3168d-cb0d-4ed6-ba79-71c4f93fb777` | `bb54296fb7276b76` | `e94b6a2dea1bcfef` | 0 |
| donchian_breakout_daily | JNJ | development | `d077e487-3a00-4ec7-b934-65098a814072` | `69651820e3adb572` | `fa6d946c77d2e4ea` | 0 |
| donchian_breakout_daily | JNJ | validation | `a233b57f-3628-4f2c-b4a4-bf33b65df5c6` | `4c56ce4318c678ce` | `60174b42c4813fc1` | 0 |
| donchian_breakout_daily | JPM | development | `3d52a63d-ff8c-464c-8373-7720a352e646` | `b459cfcad504d682` | `2ef0e70d6e28233d` | 0 |
| donchian_breakout_daily | JPM | validation | `923131c6-992e-415f-9a9b-63a4a2eb34ce` | `2fc42ff7fe1d5492` | `00ab083c2e687d53` | 0 |
| donchian_breakout_daily | MCD | development | `dcff02a7-9c21-4af4-9b7d-feea1515461a` | `923675b6e4edda40` | `781bc7e0af177cbe` | 0 |
| donchian_breakout_daily | MCD | validation | `7b2bdb20-bef9-424c-8687-a301e119bb4c` | `53a6ca43ad85f1ed` | `315d4d280fe814d7` | 0 |
| donchian_breakout_daily | MRK | development | `7fe9f6a3-e811-47f3-8436-a9fbe9ca2fd5` | `c58541af58fda935` | `0b9a21cb4c138812` | 0 |
| donchian_breakout_daily | MRK | validation | `5100bb9a-178e-46d0-aa28-99699d68ccd8` | `d26d71ebdefde956` | `1bc7b8dc16ced717` | 0 |
| donchian_breakout_daily | MSFT | development | `9cccc0fd-ac3b-44f1-bd28-af8297f6d312` | `8feaf837fc4aed05` | `e05c889b2c762408` | 0 |
| donchian_breakout_daily | MSFT | validation | `adb372ac-e939-4af3-b614-929054326ce8` | `25bb76f4f40bf563` | `07d97e1c622c8f1c` | 0 |
| donchian_breakout_daily | NKE | development | `9f719661-e4c8-4413-a124-02d89b34d45e` | `e5631a93c222128e` | `26e868321bfe26c2` | 0 |
| donchian_breakout_daily | NKE | validation | `76b743a2-dc33-4037-a7cc-6c8c2a823794` | `31961cfd7b6e9b75` | `8eaea5d96391d250` | 0 |
| donchian_breakout_daily | PFE | development | `95d5c0db-6726-4396-8122-b5f9650ffc38` | `7e80cbec5794bd59` | `75dac7052511f959` | 0 |
| donchian_breakout_daily | PFE | validation | `c97d5ca4-a6bb-489c-bba8-5dfc8d357fe4` | `e4c879a954dd4497` | `8e4fd2c52922f849` | 0 |
| donchian_breakout_daily | PG | development | `48cad422-3bfe-4c69-879d-5dd4afd7fd65` | `13b381a3898b83f2` | `a01acd573b777729` | 0 |
| donchian_breakout_daily | PG | validation | `5fc017c6-235a-4fd3-8aa8-a6bbf86d6319` | `b9ed61a325475743` | `98d4f8811d030705` | 0 |
| donchian_breakout_daily | TRV | development | `51e02105-1168-4f29-a320-0111083c7552` | `771e167d8790fd3f` | `2f1cacbf53bd94b8` | 0 |
| donchian_breakout_daily | TRV | validation | `9d381f47-b58c-4b94-8a99-92466fff1af1` | `cc7f4e29b79f3d7e` | `9b119de6dfab73c7` | 0 |
| donchian_breakout_daily | UNH | development | `31955d98-4e74-42b8-9ab1-424206afd17d` | `bcdbaefbb4db7165` | `55fb5516df00dfb8` | 0 |
| donchian_breakout_daily | UNH | validation | `20470c10-802b-4437-996d-fc8cce37a9a2` | `27dfe39f25c3f11e` | `410d34903c217566` | 0 |
| donchian_breakout_daily | RTX | development | `003de422-b64a-4e9a-ab33-18fa380ffc24` | `2de867f2e4ae5cd2` | `d320932fb2f59523` | 0 |
| donchian_breakout_daily | RTX | validation | `f8c372df-d960-472a-b9ca-2cc08687312d` | `6187ee2daf55f04f` | `356058a6a08ff87d` | 0 |
| donchian_breakout_daily | V | development | `fad6ad84-8165-417d-9ea5-cd01f5feb1cb` | `c5d907a6a937a4c9` | `a0a9f6a4811edc80` | 0 |
| donchian_breakout_daily | V | validation | `24d07508-04e8-4849-8a23-966ff2691122` | `61f236525a71182e` | `ad6057a7095623f1` | 0 |
| donchian_breakout_daily | VZ | development | `8bf371e6-e9af-4e31-8e48-c5e514ff1749` | `bdff33c3897fbe68` | `15b618d470419e12` | 0 |
| donchian_breakout_daily | VZ | validation | `691b9e6b-7189-4353-87c2-95acad52bcc5` | `2973509de88f10e5` | `c3619b1bc33cc745` | 0 |
| donchian_breakout_daily | WMT | development | `01e2ac12-e466-49e2-bfd7-03c4b101cdd5` | `6cd998499ae52ae5` | `836c7686e735e394` | 0 |
| donchian_breakout_daily | WMT | validation | `6b8c53ea-fba0-4b42-9a21-925159d176dd` | `a335de53a91ccd53` | `6ff3482d03d49e9a` | 0 |
| donchian_breakout_daily | DIS | development | `1871af52-41a4-4169-b5f4-004034b5884a` | `b9cf6d63ccbd1efe` | `67232feb7946fe50` | 0 |
| donchian_breakout_daily | DIS | validation | `831a7a81-c1fe-42ce-b5d0-a0e2d223d9c7` | `65bb1f5ac4ecdc5e` | `29e48983392f6313` | 0 |
| donchian_breakout_daily | SPY | development | `45054a02-a5ef-43ea-a903-4a4678cfc352` | `c40a3437e788ae9e` | `6e1fbb64e777e404` | 0 |
| donchian_breakout_daily | SPY | validation | `0fdf1bcd-c534-4213-af6d-01010a4e7737` | `6a97948d94f8157c` | `a79198408aca05db` | 0 |
| donchian_breakout_daily | IWM | development | `d3a07547-d4c1-4b14-8e13-5728cf298e2c` | `8b83a664d20b5597` | `74f390fb88f17fa5` | 0 |
| donchian_breakout_daily | IWM | validation | `557067a6-c279-4d1e-b1c5-a915ef048a2c` | `7b5bcf12a723e162` | `8af708509225a61a` | 0 |
| donchian_breakout_daily | DIA | development | `0afb59f4-e854-41da-a590-df0ae918732e` | `69d2fa680b9523de` | `09429a542aac415b` | 0 |
| donchian_breakout_daily | DIA | validation | `27fa9dfd-64ed-484f-9db5-56d0337d52a6` | `81752322a7f7ad08` | `b357a5e8c2324024` | 0 |
| donchian_breakout_daily | XLB | development | `15e2342e-381d-445a-84bc-1bea5cb58361` | `5c750a19e3f038d2` | `f68624c52a2e0424` | 0 |
| donchian_breakout_daily | XLB | validation | `50f51d5e-c06f-43a8-ab7d-cb02e1912a21` | `1d8a42fdfb543780` | `c36f521187c8800b` | 0 |
| donchian_breakout_daily | XLE | development | `0cd7bc52-bf79-4cc9-b5aa-0db45a57ec74` | `f75ce10f688573ec` | `acce3d8d0b749251` | 0 |
| donchian_breakout_daily | XLE | validation | `33197895-5721-48a8-a9ac-2dad8b0e30bd` | `70e46be26e7a1815` | `e32a967e30b6a4a8` | 0 |
| donchian_breakout_daily | XLF | development | `4e4794c8-c686-4abb-9403-3f12eff93515` | `5355c2b77bed5200` | `cecc7953ca158bbe` | 0 |
| donchian_breakout_daily | XLF | validation | `53eb5233-80d4-4894-a31c-2ec918e8f731` | `53f11b3f76ba858a` | `6c4894abfafb1ff1` | 0 |
| donchian_breakout_daily | XLI | development | `d4178de7-83f5-4bc6-9ea4-44b1a9cb5fdb` | `552a14764ce02f67` | `888e1e1f7d0174e0` | 0 |
| donchian_breakout_daily | XLI | validation | `d3e50e47-29d0-44a0-b006-237228bb06ba` | `1487af00f548e7de` | `6b6ffe51574ba753` | 0 |
| donchian_breakout_daily | XLK | development | `fe22128b-e573-49cb-b05b-6b239d0bc37f` | `f745e7a590f58cf1` | `ea29f33cc600a0fd` | 0 |
| donchian_breakout_daily | XLK | validation | `aeaaf45a-0cf3-4594-86a5-588acb298b79` | `24ecd03b15fd9b3a` | `a1fda3da3949d084` | 0 |
| donchian_breakout_daily | XLP | development | `d580f68e-2669-4f7e-a802-cc4e99e023cb` | `03bef29a600cb1b1` | `e352180076dba2f2` | 0 |
| donchian_breakout_daily | XLP | validation | `3c7c862f-e08d-4f69-8875-d7fea31d9847` | `38293dd94ab7a4ab` | `f0e8b953ca931bb7` | 0 |
| donchian_breakout_daily | XLU | development | `e765cc54-b0dd-48a1-b46f-343c042b5ca9` | `1573d933f6d3e00a` | `5988e7e41698705a` | 0 |
| donchian_breakout_daily | XLU | validation | `aab08b6a-22e5-4a53-b670-409434282a9d` | `a18d8c719d6b3992` | `f71d3b88affb3d35` | 0 |
| donchian_breakout_daily | XLV | development | `48cd1132-03db-48cd-81ee-e861696bed64` | `12bc26c0607e449a` | `7cd93ff1c1e920fe` | 0 |
| donchian_breakout_daily | XLV | validation | `a8d2e33a-564e-41ba-84bc-cf2c9047b7f6` | `f265f3a1ba0be192` | `074717752f163c9f` | 0 |
| donchian_breakout_daily | XLY | development | `cc6380cc-87ed-4b36-ad19-114ac99a1ed8` | `b4d9854ff392d85a` | `7e2d663162f52421` | 0 |
| donchian_breakout_daily | XLY | validation | `9857dd43-c736-4009-b0b8-795c633cd6cf` | `0d57d5e3ce3adc81` | `56a88f402945a71f` | 0 |
| time_series_momentum_daily | MMM | development | `72fcb887-63e2-47ad-886a-e85688ad7b8e` | `0a7fac6780a98183` | `839c880ee1aef922` | 0 |
| time_series_momentum_daily | MMM | validation | `e843d413-c3c6-48a4-8086-2236cbeb3471` | `b03bec9daa8a989e` | `236a5cb5bb00feb0` | 0 |
| time_series_momentum_daily | AXP | development | `56affef1-f214-49a2-8906-c2276d27296a` | `0fdaeed91e6366ed` | `e9b9b7106283e284` | 0 |
| time_series_momentum_daily | AXP | validation | `c2d602b8-f4fc-4188-b415-63996ccca90d` | `740e4a0df0522e8d` | `75cc8f28ae689b88` | 0 |
| time_series_momentum_daily | T | development | `0fd1f545-a3c0-4d4d-ae51-49b14cb3fb47` | `7c82752233d7349c` | `741a9110204357fb` | 0 |
| time_series_momentum_daily | T | validation | `e8fb22d3-fe06-428a-92d2-2149c746d03f` | `b81f9de5541efba2` | `0320eec2568d4e27` | 0 |
| time_series_momentum_daily | BA | development | `4cc5e467-a4a2-44e6-97a6-4293ad2c3545` | `2adaaeeb25bdd8ec` | `1201251b055294cb` | 0 |
| time_series_momentum_daily | BA | validation | `31f415d3-9176-436d-a665-c6ee29b3a825` | `1c48036eaa34aff4` | `bb398a8dc3372bac` | 0 |
| time_series_momentum_daily | CAT | development | `f30ddef5-ca86-4521-b771-2f9ced825323` | `7e10b571445b83b2` | `f00d72c301f3a4be` | 0 |
| time_series_momentum_daily | CAT | validation | `a697e812-0514-42a3-abb0-b7c1f6aa322a` | `b5d61a245a952c8f` | `0fa1c234bc3b655c` | 0 |
| time_series_momentum_daily | CVX | development | `59b64746-8593-4804-9067-28bbf5272fb9` | `6890dad7b4349a6a` | `653c9120035b98de` | 0 |
| time_series_momentum_daily | CVX | validation | `be1ac586-7e6e-4376-b49f-936951854cd5` | `4ce039fc3facf4b1` | `3161ed980c2ec489` | 0 |
| time_series_momentum_daily | CSCO | development | `a98d3ad0-5a0a-479f-b96f-03c7ec3e5a24` | `253cdc7087d33109` | `20dbc3a56948cbd1` | 0 |
| time_series_momentum_daily | CSCO | validation | `5e54d209-2f09-4612-9e52-4a5c809512ac` | `0a77312d04ad3c03` | `29e319c1f9a513bd` | 0 |
| time_series_momentum_daily | KO | development | `6a42bfb2-01c9-41ea-8fdb-0edcce4da57f` | `64af2844950d869c` | `9424ea607391821a` | 0 |
| time_series_momentum_daily | KO | validation | `aaf89c53-0244-4584-8bf4-9adab371c8c1` | `60a31fc4150648dd` | `14162abc8c0d2f2f` | 0 |
| time_series_momentum_daily | DD | development | `5a7f62c7-4b10-4d46-8fc6-5ffd3e192d8b` | `12e8428a54a86ad7` | `22d522c05239b08b` | 0 |
| time_series_momentum_daily | DD | validation | `e444e9f4-21b5-4aea-a7a5-9c9f3094f394` | `c3f29ef491bcee71` | `2786750ba4cf563c` | 0 |
| time_series_momentum_daily | XOM | development | `b814eee7-9191-40c3-9a57-bdadf55e3ed5` | `b4d326d19b2d724b` | `8580143d4ce9e293` | 0 |
| time_series_momentum_daily | XOM | validation | `759f65da-c98c-4ccc-a7f8-0c8c5dbe68b0` | `a2d10cc0d5f98a88` | `a5bc7f22c84dcba9` | 0 |
| time_series_momentum_daily | GE | development | `e034f82c-d984-4e5e-b270-0f7dfa7a221a` | `9a9c74ee3d391a09` | `7ae65232a00b8f93` | 0 |
| time_series_momentum_daily | GE | validation | `0aacedf9-8739-4f1c-ba9a-0cf64247694e` | `8a90713e181c0a17` | `72e5a9d7fd53d044` | 0 |
| time_series_momentum_daily | GS | development | `3add4062-2439-4ab5-8849-874018c09619` | `90238faabd26d01f` | `ee17158072f1a28e` | 0 |
| time_series_momentum_daily | GS | validation | `0bc4dfb1-9045-4971-ad95-54800ecdf14b` | `3fd310ef816df35f` | `94ee213577a86096` | 0 |
| time_series_momentum_daily | HD | development | `499aa4b5-ff50-44be-b0af-c7319e03c206` | `d036c16d4012fbec` | `e198d34e759fa757` | 0 |
| time_series_momentum_daily | HD | validation | `aa691bea-3ef1-4cc1-a749-ff4164478c25` | `0f2f3ad53bdfa81a` | `0ed21fc5e1b57baa` | 0 |
| time_series_momentum_daily | INTC | development | `878059e0-1a5b-417a-9dcb-63f0769cbc3b` | `6f8a7a0e4338882d` | `3049fc404cd897dd` | 0 |
| time_series_momentum_daily | INTC | validation | `6a5f4057-8b7a-471b-96b7-d363a688fa7c` | `be6da35ee594c3e7` | `e17c32ac6492e37a` | 0 |
| time_series_momentum_daily | IBM | development | `ed599d85-0d8b-415f-af22-e5d7d069c215` | `b65e6fa44361895a` | `ae26513d559890bb` | 0 |
| time_series_momentum_daily | IBM | validation | `2fafec0b-6226-48ab-a88f-ed3089681c2e` | `afb2ee994a39346e` | `8ff6336fcf64479c` | 0 |
| time_series_momentum_daily | JNJ | development | `6b8c1f3d-71a6-4220-97b8-61bd2b30d321` | `bfc57a1fcb84c09a` | `ca6563544ad4392c` | 0 |
| time_series_momentum_daily | JNJ | validation | `da79b693-cdf2-4122-89eb-07301b67856b` | `196f1772290f04f4` | `6bb844c76dba91e6` | 0 |
| time_series_momentum_daily | JPM | development | `c1a6dc28-eda5-4d63-b4cf-f3170d2f710c` | `156816d64cbfb02c` | `e05ee4bf3f40e439` | 0 |
| time_series_momentum_daily | JPM | validation | `ff80d7fc-8eb7-4b31-aac2-e8b7e8ea5e72` | `82c62cc9cdbb03ae` | `c04f7185efd20a79` | 0 |
| time_series_momentum_daily | MCD | development | `ccd85ea3-bf48-47c9-b2e6-f656fc03deb1` | `e433a59c3fa3bcca` | `4808d4d3853c2e3d` | 0 |
| time_series_momentum_daily | MCD | validation | `b1dfdafc-c587-43f0-aa13-39bbff99c25c` | `839386e522f1edd7` | `f7b79ac7f4dea7bf` | 0 |
| time_series_momentum_daily | MRK | development | `4ef3e646-afc0-4cf4-8a47-d02f249ac42e` | `c770779313ddeb52` | `3a9e8539ff91d4c5` | 0 |
| time_series_momentum_daily | MRK | validation | `a0808219-0432-4c40-9d0f-e4cfed83be01` | `3ee631804f75376b` | `d3b83434bb161051` | 0 |
| time_series_momentum_daily | MSFT | development | `1a81e5de-87b4-4ec1-bff5-0ee23cee450c` | `235636b4b3a9faae` | `f989de5107a72ddd` | 0 |
| time_series_momentum_daily | MSFT | validation | `c8553eaa-bfb5-4f06-96da-32d6aac412e0` | `5e25937c17325686` | `77fcdfffb6531ccc` | 0 |
| time_series_momentum_daily | NKE | development | `7225c837-c597-4bbf-831b-64315cf6ff60` | `a63bcde78210ed21` | `1af6f6907b319b8e` | 0 |
| time_series_momentum_daily | NKE | validation | `b74fdc27-7201-40a1-90c4-270362a648a3` | `622f814453f65908` | `4a28bb50c3d5493d` | 0 |
| time_series_momentum_daily | PFE | development | `9876caa3-f0ce-4e80-ba50-7a61d3a86e74` | `3fc862584eaadce1` | `a60fac84af963fcd` | 0 |
| time_series_momentum_daily | PFE | validation | `dbdb2919-2c69-46a8-8de8-4bacaf89792d` | `8b725989104284ee` | `97aba9d38ef40c97` | 0 |
| time_series_momentum_daily | PG | development | `beff43b7-9c66-4139-923e-22d72a3c7c81` | `47766e622fb5a7cc` | `39640d137c140fdb` | 0 |
| time_series_momentum_daily | PG | validation | `ed70eb7a-ba74-4cf5-a6cb-d5a55f94877e` | `8e36996f0a0a17f3` | `9b2fb5c15861cb04` | 0 |
| time_series_momentum_daily | TRV | development | `41f2d950-d828-4b8b-b9a2-6358e5d33b7c` | `4220c8e6deebdee5` | `30233099b3a2a50d` | 0 |
| time_series_momentum_daily | TRV | validation | `5ffd7dc4-5b32-4103-837b-ea5642c2cb4c` | `aa0b5e6b73d17a8b` | `265e055453b9bbf6` | 0 |
| time_series_momentum_daily | UNH | development | `dbb24c3f-1dfd-441a-8787-c052ac760fc2` | `1b886af6dc1718ad` | `0a9e988cb6c8a24a` | 0 |
| time_series_momentum_daily | UNH | validation | `c24bd4ee-5f99-425a-8b43-ac981810ea31` | `49bad52026b9d05d` | `78a1b233a4cededf` | 0 |
| time_series_momentum_daily | RTX | development | `abb0bc97-3c0c-4ea6-9ab8-6c0389d3aae4` | `dd25b57dfbbc01d8` | `c176c4633a414f66` | 0 |
| time_series_momentum_daily | RTX | validation | `69d7bcd8-0480-4d86-ae4f-855f039e4450` | `9486ae888bc6fb57` | `b3005f9c2639790b` | 0 |
| time_series_momentum_daily | V | development | `53314e3f-10d6-472d-98c5-42a51f40eb28` | `e121ef4235702c59` | `7261faed8c15716e` | 0 |
| time_series_momentum_daily | V | validation | `197b4eab-b87f-4028-b6fa-b2307449b737` | `bcd173b407b84970` | `9af50d48bf11393b` | 0 |
| time_series_momentum_daily | VZ | development | `2e1fec1e-08be-4dd6-86d9-8104e6892e3e` | `442a4c045306f008` | `bfba7c87548f4c57` | 0 |
| time_series_momentum_daily | VZ | validation | `1f15bf4d-6a22-41a5-b29e-18dd1851fa08` | `f8e90d96e59d7f82` | `4cfcb52e62ff21de` | 0 |
| time_series_momentum_daily | WMT | development | `e2245068-503a-4008-8b31-d64cdf80b6b5` | `f260c6f10490bd11` | `bb7cd5d7c26e5dc7` | 0 |
| time_series_momentum_daily | WMT | validation | `7486d643-2579-4c0d-ad6a-193fa3bf47dc` | `30534bf07bd3e7a2` | `f0aa144224206b53` | 0 |
| time_series_momentum_daily | DIS | development | `12b6a149-8047-4828-a5c5-f99273b54a5d` | `6ddc47da57819490` | `a9426720e0ce6564` | 0 |
| time_series_momentum_daily | DIS | validation | `3a783407-5c50-43b4-84b0-07ab33086cc5` | `617c46843c2d3813` | `e86a5d0d35f1ca47` | 0 |
| time_series_momentum_daily | SPY | development | `c95a5657-fd7c-4852-bc5d-470a65427f68` | `1c7e798088131694` | `5511c8a6e7f282c0` | 0 |
| time_series_momentum_daily | SPY | validation | `46db861f-08c4-49bd-b5b4-d4d192594114` | `3aa64c53a06e47c6` | `d5f20615c81ded63` | 0 |
| time_series_momentum_daily | IWM | development | `82f650b7-6f18-408d-ba29-f8abec9d1257` | `5a78b87d161749ba` | `76f946519c92638f` | 0 |
| time_series_momentum_daily | IWM | validation | `57c6ee83-d524-4196-b09d-0324d64540f8` | `5eeb6e44143cf808` | `43e9dd9e7ca7685c` | 0 |
| time_series_momentum_daily | DIA | development | `7a3ef79d-3fec-4adc-9a27-db589dbe529f` | `e5b7b5253debaa12` | `e38124df11310bef` | 0 |
| time_series_momentum_daily | DIA | validation | `0bd53276-eb03-4926-9941-f4c2066e1e79` | `30ceebcda7312997` | `449164cf896c5ef2` | 0 |
| time_series_momentum_daily | XLB | development | `2bdc9720-9dcb-4077-a0fa-68bebcf50117` | `e64731d1de414611` | `dbf24bc03860cd77` | 0 |
| time_series_momentum_daily | XLB | validation | `edb1d80e-1a25-42c4-9cad-c9fa0a8610da` | `24a991d2c042c13f` | `acaf7fd56273bc70` | 0 |
| time_series_momentum_daily | XLE | development | `cd4af89b-6bb0-452a-8c62-a0370c13ac84` | `5251450503d7d31f` | `ea1adb9da0ec5d46` | 0 |
| time_series_momentum_daily | XLE | validation | `9db3287f-ff17-47a4-9d95-326cd6048a0b` | `05e749f3b337fbac` | `02df9b2a215c5233` | 0 |
| time_series_momentum_daily | XLF | development | `b5cfa496-6083-41cd-93b4-d5f2a5abe45e` | `7ff5be14f53d268e` | `8a47c1c237b32bba` | 0 |
| time_series_momentum_daily | XLF | validation | `39267e91-dc52-4069-823c-5e3d79201578` | `dbb2df5efc5d37bd` | `590d2cb47e6cf086` | 0 |
| time_series_momentum_daily | XLI | development | `5b4e1e9c-1778-4bb4-83f9-858409754511` | `b213f6f919f3d1ed` | `85d0539a72374a07` | 0 |
| time_series_momentum_daily | XLI | validation | `3e40058d-3c10-42fe-9d38-5965d8c4a760` | `3ef3f057b1ad06ae` | `8108413250cc9e95` | 0 |
| time_series_momentum_daily | XLK | development | `7dab2397-74c0-4b58-9f8a-5a0ac8061dfb` | `a75be2c9ac4f9bcc` | `15fce7aa987cf5f7` | 0 |
| time_series_momentum_daily | XLK | validation | `95d136fa-3666-4261-a0dc-bf32588ae1dd` | `d0a76578eed72d8b` | `4042e8a54d2c9edb` | 0 |
| time_series_momentum_daily | XLP | development | `d9788844-b7d0-45db-be5b-f5caf2a6362e` | `d56c3bef26e753e3` | `dcdcf10e7fabd5bf` | 0 |
| time_series_momentum_daily | XLP | validation | `44d9a15a-afea-4710-8141-1f596b0bb5b9` | `db7f6dfc4031ca24` | `0aee4c5bd4500f3b` | 0 |
| time_series_momentum_daily | XLU | development | `173f44df-0352-47ec-9c83-804fef784d0a` | `125e9b6bfb661c0d` | `13b323b394637091` | 0 |
| time_series_momentum_daily | XLU | validation | `c6f73e6b-de29-4b7d-8a53-7aeb8d99c930` | `0b3aef0fd4d690ca` | `e269f0602858e82c` | 0 |
| time_series_momentum_daily | XLV | development | `cf9b9ded-63cb-42f1-b8f2-618d58a4f99f` | `bf4a22ff0566a264` | `a4db44e84de33bd8` | 0 |
| time_series_momentum_daily | XLV | validation | `eb6c51f2-ccc7-4ed3-b73b-da53f5eab4bb` | `92bf2a8f93b06263` | `44ed640df8b655e4` | 0 |
| time_series_momentum_daily | XLY | development | `33c11a8f-d7d0-493b-8675-b39ed82273ce` | `ab25181022786134` | `fe9ec7600aa8fbe7` | 0 |
| time_series_momentum_daily | XLY | validation | `d90280d4-1730-4c5d-8b9b-bdfbb918f8f1` | `2d163619180aab6d` | `91a6ec5fe228a988` | 0 |
| time_series_momentum_daily | MMM | final_test | `c7d38070-4d1c-47ef-a0d3-1d1f4ed4714e` | `97403988b1f13bac` | `6961e3c3199ab8fd` | 0 |
| time_series_momentum_daily | AXP | final_test | `415f5ea6-a637-4efd-989c-85a4d0dd87ec` | `f9ec2ed876c53795` | `2d1190d07e0f7203` | 0 |
| time_series_momentum_daily | T | final_test | `02f69283-4699-4982-86d8-4449faa6cd44` | `9d24555afe18de61` | `afff89e2b8122932` | 0 |
| time_series_momentum_daily | BA | final_test | `f6e703b3-48fa-458e-9e89-4d7b9e3aedb9` | `66ddbe52d7918df3` | `734af79164cd7372` | 0 |
| time_series_momentum_daily | CAT | final_test | `9a3c48e5-c066-4213-bb82-c3813af34cd5` | `0b4392f6e6c255f4` | `2a7a0cfcd1e0adf0` | 0 |
| time_series_momentum_daily | CVX | final_test | `f96fae14-a378-4f3d-820c-41bd2b47405d` | `49147af91b2ec129` | `47a2a2520a7cb229` | 0 |
| time_series_momentum_daily | CSCO | final_test | `48dd9f4e-6f31-469a-b56d-c60b3174204e` | `c33d91abda538acc` | `93527bce09fdcc4a` | 0 |
| time_series_momentum_daily | KO | final_test | `cc6c815e-88a1-4c22-b794-b86ea924c449` | `8a99f58e2e6b6ac7` | `40843ff9e161136e` | 0 |
| time_series_momentum_daily | DD | final_test | `f0e5b71f-ec81-49d8-93f1-1d97a2893246` | `8f772567f348e7e9` | `94255306f4d0f106` | 0 |
| time_series_momentum_daily | XOM | final_test | `f9048d62-a40c-472e-b8dd-61e119ebb458` | `04e030bf302212df` | `03b601e79933483a` | 0 |
| time_series_momentum_daily | GE | final_test | `cbbc39e5-3973-4207-ae1f-fb114baa225a` | `8b48b63010dbffba` | `a31b8efa852506dc` | 0 |
| time_series_momentum_daily | GS | final_test | `cd2ca008-a452-429d-87b9-e2e453d3ff26` | `f278b8753c946bb6` | `7381d394e5b2b953` | 0 |
| time_series_momentum_daily | HD | final_test | `7fb88d4e-d7f5-4cdd-b326-441f6ce5b439` | `2947e5087a077eca` | `c5be5ecf25482266` | 0 |
| time_series_momentum_daily | INTC | final_test | `1922beb5-cc41-41d6-baa2-bc62f212faa2` | `519a8d618642ce59` | `0562d5fc16c6b48b` | 0 |
| time_series_momentum_daily | IBM | final_test | `3a75e3bc-56f5-41fe-ae66-77b32e3576b2` | `4cad2cb2bb76499b` | `5433047b149c0b64` | 0 |
| time_series_momentum_daily | JNJ | final_test | `79ae5816-53e7-4d0a-a27f-dd1e59d44ebd` | `49bc4e2e4b1f3026` | `0730c36da2df630e` | 0 |
| time_series_momentum_daily | JPM | final_test | `3c29d170-8b93-4b7d-88c3-c8d4601065cf` | `afb4b24c9840684a` | `87b79d732f5e18b3` | 0 |
| time_series_momentum_daily | MCD | final_test | `47c33307-5dd0-4625-b928-23a565305a88` | `678e6c5102d6c31b` | `fae2911783f4631e` | 0 |
| time_series_momentum_daily | MRK | final_test | `c1885acd-1093-4bdd-b400-8fea75a3aed9` | `0b4979d2efcf4739` | `e33948765a13f554` | 0 |
| time_series_momentum_daily | MSFT | final_test | `fa8ba8a2-791a-4cdd-8d41-8a07039bcba6` | `89c75794e7fb2ba8` | `d823fc714e335e28` | 0 |
| time_series_momentum_daily | NKE | final_test | `12ffdec5-4950-4083-a07d-103b91553f15` | `5be8eedf76638283` | `3d814821bcd5362e` | 0 |
| time_series_momentum_daily | PFE | final_test | `8c323fbf-0b0f-4574-9f9e-301acfe096ec` | `537e5c0b141dedde` | `c463c61ea459c1f0` | 0 |
| time_series_momentum_daily | PG | final_test | `f8dd863f-2fc3-4a2a-bcde-4d3981b4c4c4` | `bb40390bbcc61fc9` | `970df53bfe8ff45a` | 0 |
| time_series_momentum_daily | TRV | final_test | `30241107-6bb9-4116-82ae-248bcb708686` | `e830d97315fececc` | `86e65f206609db2b` | 0 |
| time_series_momentum_daily | UNH | final_test | `a09f0bd5-08a1-4474-ac96-b9e411fd48fa` | `7930734618981d42` | `9d65609b09e3a15d` | 0 |
| time_series_momentum_daily | RTX | final_test | `62a8f5a2-26c6-424b-a7ab-159a1c830b66` | `8a320b10cb1e114c` | `2ffffd689fc3561a` | 0 |
| time_series_momentum_daily | V | final_test | `4e1c6067-d2cc-4cd1-8d9b-f175e1e445e0` | `4f8aa136195e913e` | `3c4017fb5303c830` | 0 |
| time_series_momentum_daily | VZ | final_test | `3ee8ea2e-7bbe-4394-bd75-421b78e76cf7` | `d9e5f1065c472a02` | `3826f1e56e39d450` | 0 |
| time_series_momentum_daily | WMT | final_test | `b5c707ab-644d-4fdd-b49d-2a0866e05833` | `245f6cfc84dad0a5` | `5e85197ba7555534` | 0 |
| time_series_momentum_daily | DIS | final_test | `c5902fd8-2d98-4026-9b1f-22cd7d926593` | `788aaa9aa6da88ba` | `6cf8d57b00f54fa5` | 0 |
| time_series_momentum_daily | SPY | final_test | `0c2a17ad-6b9e-4e7a-b8a5-3207ad3309f8` | `2e9ab5db697ab1bc` | `f2bd029b4973d64e` | 0 |
| time_series_momentum_daily | IWM | final_test | `e8a21111-d1b4-47c3-b1c8-93d79d5ea294` | `57a3d0d895604dc6` | `241a10e82b87185d` | 0 |
| time_series_momentum_daily | DIA | final_test | `12047aea-2952-4031-abc3-721df6f3a81a` | `cff592328af67266` | `c8e7f9b1b236538d` | 0 |
| time_series_momentum_daily | XLB | final_test | `2324bed8-524f-4595-88b1-92f322daf33c` | `b696a195a5b5d0e8` | `cd1deb762b4185c9` | 0 |
| time_series_momentum_daily | XLE | final_test | `1519619a-5f55-4118-a37d-9dbc4807ff36` | `be202e639d16ed22` | `211d087d83d4018d` | 0 |
| time_series_momentum_daily | XLF | final_test | `db9ed5a9-b46a-4a3a-a203-d057fd570f5a` | `3ba3b8214ab93bd4` | `da8601e7e7603545` | 0 |
| time_series_momentum_daily | XLI | final_test | `c2769113-e3e6-4e41-9c2b-253586cc9c6f` | `ddb2b9ad9362504c` | `a79977ef69597cf1` | 0 |
| time_series_momentum_daily | XLK | final_test | `0e1fbbbc-def5-4a49-9e15-79b1a38952bb` | `c617a33e88c1e93a` | `1b6fea24b64a704f` | 0 |
| time_series_momentum_daily | XLP | final_test | `590746f5-b727-46f2-b957-1fc5042c1345` | `4ec437db1b757c5b` | `c0eb01c620dbec88` | 0 |
| time_series_momentum_daily | XLU | final_test | `5dcd1a43-9c8e-4bb3-9258-d77a480d3a2f` | `67e7300ea79844ca` | `d906e1de6afb90ce` | 0 |
| time_series_momentum_daily | XLV | final_test | `be408283-46b9-4b1b-91fc-e857ac7c2fad` | `d3f28fdaa0316cbf` | `9c0d79b67644efdb` | 0 |
| time_series_momentum_daily | XLY | final_test | `3d16399f-aacf-43e4-8f36-95be80f0ee86` | `f39c425b8245e325` | `27177da3fc5fd0ad` | 0 |

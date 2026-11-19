# Strategy Specification v2: Contract

**Status:** implemented 2026-11-19 (signals product, Step 1 of `docs/SIGNALS-PLAN.md`).
v2 is v1 plus one required block. Everything in `docs/STRATEGY-SPEC-V1.md` (series,
indicators, conditions, history derivation, evaluation at the close) applies unchanged.
A v1 document stays valid, evaluates as before and keeps its `spec_sha256`.

Implemented by `strategies/spec/` (schema, validator, explanation) and
`strategies/trade_rules.py` (the entry and exit rules, shared by the backtest engine and
the daily signal engine).

## 1. Document shape

```yaml
spec_version: 2
name: Trend following 50/200 with a stop and a target
timeframe: daily
direction: long_only
indicators:
  sma_fast: {type: sma, source: close, window: 50}
  sma_slow: {type: sma, source: close, window: 200}
entry:
  all_of:
    - {left: close, op: gt, right: sma_slow}
    - {left: sma_fast, op: gt, right: sma_slow}
exit:
  any_of:
    - {left: close, op: lt, right: sma_fast}
risk:                                   # required in v2, refused in v1
  stop: {type: percent, value: 5}       # percent below the reference price; 0 < value <= 50
  target: {type: risk_multiple, value: 2}  # multiple of the planned risk; 0 < value <= 20
  min_reward_risk: 1.5                  # 0 <= value <= 20
```

`stop.type` has one value, `percent`. `target.type` has one value, `risk_multiple`.
The three numbers are canonicalised like every other constant (`5`, `5.0` and `"5"` hash
the same) and are part of `spec_sha256`.

## 2. Levels of a signal

A signal is produced after the close of session D.

- `reference` = the close of D.
- `stop` = `reference × (1 − stop.value / 100)`.
- `target` = `reference + target.value × (reference − stop)`.

All three are rounded to six decimals, stored with the signal and never changed.

## 3. Entry (the open of session D+1)

The signal expires without a trade when the open is:

| Condition | Code |
|---|---|
| at or below the stop | `expired_open_at_or_below_stop` |
| at or above the target | `expired_open_at_or_above_target` |
| such that `(target − open) / (open − stop) < min_reward_risk` | `expired_reward_risk_below_minimum` |

Otherwise the trade opens at the open. R is measured against the actual initial risk,
`entry − stop`.

Example: reference 100, stop 95, target 110. An open at 111 expires. An open at 102
gives 8 against 7, a ratio of 1.14; it opens only if `min_reward_risk` is 1.14 or lower.

## 4. Exit (every session while the trade is open, the entry session included)

In this order:

1. The open is at or below the stop, or at or above the target: exit at the open.
2. Otherwise, the exit rule was true at the previous close: exit at the open.
3. Otherwise the session's low and high are checked. Only the stop touched: exit at the
   stop. Only the target touched: exit at the target. Both touched: exit at the stop
   with `intrabar_order_unknown` set.

Exit reasons are a closed list: `stop`, `target`, `rule`.

The strategy is still evaluated at each close, statelessly. An `EXIT` while flat and a
`LONG` while in a trade are ignored. A trade closed by its stop or target during a
session is flat at that session's close, so a `LONG` on that close issues a new signal.

## 4a. Result in R

Both are measured against the actual initial risk: the entry price the rules gave (the
open of the entry session) minus the fixed stop. The planned risk (reference minus stop)
is not the denominator.

- Gross R: `(rule exit price − rule entry price) / (rule entry price − stop)`. Rule
  prices are the open, the stop or the target, before any cost. The daily engine and
  the backtest must agree on it for every signal.
- Net R: `net profit / (quantity × (rule entry price − stop))`, where the net profit
  is what the backtest's cost model leaves (slippage on both fills, both commissions).
  Only the backtest has it; the signal log simulates no costs.

A backtest trade stores `rule_entry_price` and `rule_exit_price` beside its fill prices.

## 5. Error codes added to the closed list

| Code | When |
|---|---|
| `stop_type_not_supported` | `risk.stop.type` is anything but `percent` (ATR, indicator, trailing) |
| `target_type_not_supported` | `risk.target.type` is anything but `risk_multiple` |

Existing codes cover the rest: a missing `risk` block or field is `missing_field`; a value
outside its bounds is `parameter_out_of_bounds`; an extra key inside `risk` is
`unknown_field`; `direction: short` is `direction_not_supported`; a top-level `stop_loss`,
`take_profit` or `trailing_stop` is `stop_or_target_price_not_supported`; a `risk` block on
a `spec_version: 1` document is `position_sizing_in_strategy_not_supported`, as before.

## 6. Explanation

For a v2 document the renderer adds the reference price, the stop, the target, the entry
expiry rule with the minimum reward-to-risk, and the exit order. The v1 text is unchanged.

## 7. Not in v2

Short signals, ATR stops, indicator stops, trailing stops, moving a stop after entry,
partial exits. Each is refused with a code from the closed list.

## 8. Tests

`tests/test_trade_rules.py` (the rules as pure functions),
`tests/test_strategy_spec_v2.py` (validation, hashing, explanation, the four v1 digests
pinned) and `tests/test_signals_step1_engines.py` (the same cases through the backtest
engine and the daily engine on a migrated database: the final state, rule prices, exit
reason, flag and gross R of every signal, and the `min_reward_risk` boundaries).
`tests/test_signal_study.py` covers the metrics in R, the selection rules and the study
runner.

# Strategy Specification v1: Contract

**Status:** planning contract (2026-10-07). Consumed by the YAML editor, the AI assistant, the validator, the interpreter, the explanation renderer, the later visual builder and the later Pine generator. No implementation yet.
**Legend:** **[Settled]** required by the approved product direction · **[Chosen]** implementation choice made in planning, changeable without a product decision · **[Open]** needs the user.

---

## 1. Purpose and bounds

One canonical, declarative specification describes a daily, long-only, single-asset rule set. One interpreter executes it. Every authoring method produces this specification and nothing else. **[Settled]**

Not expressible in v1, and reported as such: shorting or long-short, intraday or weekly bars, intrabar stop or target prices, trailing stops, position sizing inside the strategy, conditions on other assets, pyramiding, arbitrary code, negative (look-ahead) shifts. **[Settled]**

---

## 2. Document shape

```yaml
spec_version: 1                      # literal 1
name: Trend following 50/200         # 1..80 chars
description: >-                      # 0..2000 chars
  Long when price is above the slow average and the fast average is above the slow one.
timeframe: daily                     # literal
direction: long_only                 # literal
indicators:                          # 0..12 named instances; names ^[a-z][a-z0-9_]{0,31}$
  sma_fast: {type: sma, source: close, window: 50}
  sma_slow: {type: sma, source: close, window: 200}
entry:                               # condition tree
  all_of:
    - {left: close, op: gt, right: sma_slow}
    - {left: sma_fast, op: gt, right: sma_slow}
exit:                                # condition tree
  any_of:
    - {left: close, op: lt, right: sma_fast}
```

Canonical form **[Chosen]**: keys sorted, defaults made explicit (`shift: 0`), numbers as decimal strings, serialized to JSON for `spec_sha256`. The YAML text the user wrote is stored verbatim beside the canonical JSON.

---

## 3. Series and indicators

| Term | Parameters | Unit | Mathematical minimum (bars) | History fed | Pinned formula |
| --- | --- | --- | --- | --- | --- |
| `open`, `high`, `low`, `close` | `shift` | price | 1 + shift | — | the bar value `shift` sessions back |
| `volume` | `shift` | volume | 1 + shift | — | adjusted volume of the study series |
| `sma` | `source`, `window` (1..500), `shift` | unit of source | window + shift | window | arithmetic mean of the last `window` values of `source` ending `shift` sessions back **(original `_compute_sma`)** |
| `ema` | `source`, `window` (1..500), `history` (≥ window, ≤ 1000), `shift` | unit of source | window + shift | `history` | seed = simple mean of the first `window` values of the `history`-bar list; then `ema = α·x + (1−α)·ema` with `α = 2/(window+1)` over the remaining values **[Chosen]** |
| `rsi` | `source`, `window` (2..200), `history` (≥ window + 1, ≤ 1000), `shift` | dimensionless (0..100) | window + 1 + shift | `history` | Wilder: changes between consecutive values of the `history`-bar list; seed average gain and loss = simple means over the first `window` changes; then `avg = (avg·(window−1) + x)/window` over the remaining changes; both averages zero → 50; loss zero → 100; gain zero → 0 **(original `_compute_rsi`)** |
| `highest` | `source` (`high`/`low`/`close`/`open`), `window`, `shift` | price | window + shift | window | maximum of `source` over the `window` bars ending `shift` sessions back **(original `_compute_channels` with `shift: 1`)** |
| `lowest` | same | price | window + shift | window | minimum, same convention |
| `lag` | `source`, `periods` (1..500), `shift` | unit of source | periods + 1 + shift | — | the value `periods` sessions before the bar `shift` sessions back **(original TSM `closes[-(lookback+1)]`)** |
| `change_pct` | `source`, `periods`, `shift` | dimensionless | periods + 1 + shift | — | `(x[t] / x[t−periods]) − 1` **[Chosen]** |

Bars are the sessions the study's access layer returns for the asset on or before the evaluation date; "sessions back" means positions in that list, never calendar days. **[Settled, matches the originals]**

`history` is explicit for recursive indicators because their values depend on how many bars feed the recursion. The specification records both numbers: the mathematical minimum and the history fed; the bars actually required also include the term's `shift` and the extra evaluation a crossing condition needs (§5). **[Settled by corrections 1 and 2]**

---

## 4. Conditions

`{left, op, right}` where `left` is a series or indicator name; `right` is a series, an indicator name, or a constant; `op ∈ gt, ge, lt, le, crosses_above, crosses_below`. **[Settled]**

- `crosses_above`: `left[0] > right[0]` and `left[1] ≤ right[1]`, where `[o]` is the value at offset `o` per §5; `crosses_below` mirrored. Both operands are evaluated at offsets 0 and 1 with their own complete windows. **[Chosen]**
- Combinators `all_of` and `any_of`, nesting depth ≤ 3, at most 16 leaf conditions per document. **[Settled bounds]**
- A unit check applies to every comparison: both sides must share a unit, or the right side is a constant. A constant against a `price`-unit term marks the specification `price_scale_dependent`; a constant against a dimensionless term does not. A `price` term against a `volume` term is an error. **[Settled by correction 2]**

---

## 5. History derivation and slicing (exact rules)

Let `bars` be the list of loaded bars in ascending session order, `n = len(bars)`. A term is evaluated **at offset `o`** (0 = the current bar, 1 = the previous evaluation) by slicing its own complete window from the end of `bars`:

| Term | Window length `W` | Slice for the value at offset `o` | Bars needed at offset `o` |
| --- | --- | --- | --- |
| price or volume series with `shift` | 1 | `bars[n − (1 + shift + o)]` | `1 + shift + o` |
| `sma`, `highest`, `lowest` | `window` | `bars[n − (window + shift + o) : n − (shift + o)]` | `window + shift + o` |
| `ema`, `rsi` | `history` | `bars[n − (history + shift + o) : n − (shift + o)]`; the recursion runs over exactly these `history` bars | `history + shift + o` |
| `lag(periods)` | `periods + 1` | first element of `bars[n − (periods + 1 + shift + o) : n − (shift + o)]` | `periods + 1 + shift + o` |
| `change_pct(periods)` | `periods + 1` | last over first of the same slice, minus 1 | `periods + 1 + shift + o` |
| constant | 0 | — | 0 |

Rules **[Settled by correction 2]**:
- `shift` adds to the bars needed; it never shortens the window. RSI with `history: 100` and `shift: 20` needs 120 bars and its value is the full 100-bar recursion ending 20 bars back.
- A crossing condition needs both operands at offset 0 **and** at offset 1. The offset-1 value of a recursive term is computed by its own complete recursion over the window ending one bar earlier (`history` bars), never by taking the previous iterate of the offset-0 recursion, which is a differently seeded series.
- `bars_needed(condition)` = max over its operands of `bars_needed(term, 0)`, or of `bars_needed(term, 1)` for `crosses_above` / `crosses_below`.
- `history_required` = max over all leaf conditions of `bars_needed(condition)`; at least 1. The interpreter loads exactly `history_required` sessions on or before the evaluation date. Fewer loaded bars → `FLAT` with `rule_insufficient_history`.
- `history_minimum` = the same computation with each recursive term's `history` replaced by its mathematical minimum (`window` for `ema`, `window + 1` for `rsi`). It is informational: it shows how much of `history_required` exists only to reproduce a recursion.

Worked examples: `rsi(14, history 100)` → 100; `rsi(14, history 100, shift 20)` → 120; `rsi(14, history 100)` in `crosses_above 30` → 101; `sma(50) crosses_above sma(200)` → 201; `highest(high, 55, shift 1)` → 56; `lag(close, 252)` → 253.

Other derived values stored on every version: `scale_class ∈ {price_scale_free, price_scale_dependent}`, `terms_used`, `operators_used`, `pine_equivalent_available`.

## 6. Evaluation semantics (stateless, as the originals)

On each evaluation date, with `bars` = the last `history_required` sessions on or before the date **[Settled]**:
1. If `len(bars) < history_required`, or any indicator is undefined → `FLAT`, reason `rule_insufficient_history`.
2. Else if `exit` is true → `EXIT`, reason `rule_exit`.
3. Else if `entry` is true → `LONG`, reason `rule_entry`.
4. Else → `FLAT`, reason `rule_no_signal`.

The interpreter does not consult position state. An `EXIT` while flat is ignored by the engine, as today; a `LONG` while in a position is ignored, as today. Exit-before-entry precedence matches trend following, the only original where both rules can be true on the same close. **[Settled by correction 1]**

Comparisons use `Decimal`; indicator arithmetic uses `Decimal` with the originals' division order. **[Chosen]**

---

## 7. Error codes (closed)

`unknown_field`, `missing_field`, `invalid_type`, `spec_version_unsupported`, `timeframe_not_supported`, `direction_not_supported`, `unsupported_indicator`, `unsupported_source`, `unsupported_operator`, `parameter_out_of_bounds`, `history_below_minimum`, `undefined_reference`, `self_reference`, `unit_mismatch`, `nesting_too_deep`, `too_many_conditions`, `too_many_indicators`, `lookahead_reference_not_supported`, `stop_or_target_price_not_supported`, `position_sizing_in_strategy_not_supported`, `multi_asset_condition_not_supported`, `name_invalid`, `description_too_long`.

The AI assistant's `unsupported_requests` list uses the same codes plus `other_unsupported_request` with free text. **[Settled]**

---

## 8. The four originals as specifications (reconciled)

| Original | YAML warm-up | Specification terms | `history_required` | Notes |
| --- | --- | --- | --- | --- |
| trend_following_daily | 200 | `sma_fast: sma(close, 50)`, `sma_slow: sma(close, 200)`; entry `close > sma_slow and sma_fast > sma_slow`; exit `close < sma_fast` | 200 | exit before entry, as the original |
| rsi_mean_reversion_daily | 100 | `rsi14: rsi(close, 14, history: 100)`; entry `rsi14 < 30`; exit `rsi14 > 70` | 100 | `history: 100` reproduces the 100-bar Wilder window; minimum would be 15 |
| donchian_breakout_daily | 56 | `ch_high: highest(high, 55, shift 1)`, `ch_low: lowest(low, 20, shift 1)`; entry `close > ch_high`; exit `close < ch_low` | 56 | preceding-bar channels via `shift: 1` |
| time_series_momentum_daily | 253 | `ref: lag(close, 252)`; entry `close > ref`; exit `close <= ref` | 253 | the original emits `EXIT` whenever not `LONG`; same here |

Equivalence is not claimed until section 9 passes. If a difference survives, the seeded version records `behaviour_differs_from_original` with the reason.

---

## 9. Parity test matrix (must pass before the examples are seeded)

For each original × fixture: run the Python strategy and the interpreter on the same bars through the same access layer, compare `(session_date, direction)` for every evaluation date, and compare indicator values where the original exposes them (`sma_short`, `sma_long`, `rsi`, channel values, lookback close) with tolerance 0.

Fixtures:
1. **Below history:** `history_required − 1` bars available → both `FLAT`.
2. **Exact history:** exactly `history_required` bars → both evaluate.
3. **Long history:** `history_required + 300` bars → both evaluate; for RSI also run with `history: 50` and `history: 300` and assert the signals differ from the `history: 100` run on at least one date, proving the dependence is real and the parameter necessary.
4. **Gap inside the window:** one missing session for the asset inside the last `history_required` sessions → both `FLAT` (the loader returns fewer bars).
5. **Threshold adjacency:** closes constructed so RSI sits within 0.01 of 30 and 70, closes within one tick of the Donchian channel, and `close == lag` for momentum (`<=` must exit).
6. **Exit and entry both true** (trend following only): `sma_slow < close < sma_fast` with `sma_fast > sma_slow` → `EXIT`.
7. **Determinism:** two runs of the interpreter on the same inputs are byte-identical.

Additional interpreter tests (no original to compare with; the contract is the oracle):
8. **Shifted recursion:** `rsi(14, history 100, shift 20)` derives `history_required = 120`; its value equals `rsi(14, history 100)` evaluated 20 sessions earlier on the same series; with 119 bars the result is `FLAT` / `rule_insufficient_history`.
9. **Crossing:** `sma(50) crosses_above sma(200)` derives 201; the offset-1 operand values equal the offset-0 values computed one session earlier; for an RSI operand the offset-1 value equals a fresh full recursion over the window ending one bar earlier and differs from the previous iterate of the offset-0 recursion on a fixture built to expose it.
10. **Early-history boundaries for every term type:** `history_required − 1` bars → `FLAT`; exactly `history_required` → evaluated; `+ 1` → evaluated with the newest bars only.

---

## 10. Explanation renderer (deterministic)

Output sections: name and description; "Enter long when …" with one numbered line per leaf condition and the combinator words "all of" / "any of"; "Exit when …" likewise; "History required: N sessions (mathematical minimum M)"; "Price-scale: free | depends on absolute price levels"; "This strategy does not: short, use intraday data, place stop or target prices, size positions" (static list). Same specification → same text (hash-pinned). **[Chosen]**

---

## 11. Open items for this contract

- **[Settled 2026-10-07]** `ema` ships in v1.
- **[Open]** Maximum `history` for recursive indicators (1000 proposed).

---

## 12. Implementation notes (2026-10-07)

- Numeric constants are normalised on load (`30`, `30.0`, `"30"` become the same Decimal), so the canonical JSON and `spec_sha256` are stable across YAML and JSON round-trips.
- `change_pct` is undefined (treated as insufficient history) when the reference value is zero.
- Below `history_required` the interpreter exposes no indicator values; the originals expose partial ones. Directions agree; the parity tests compare values only at or beyond warm-up.
- `ema` shipped in v1 with the documented seed and recursion; it has no original to compare with and is covered by the §9 item 8 and 10 tests.
- A bare series operand (`open`, `high`, `low`, `close`, `volume`) carries no `shift` in v1 YAML: an operand is a plain name. "The close N sessions back" is written as a `lag` indicator (`{type: lag, source: close, periods: N}`), which derives the same `periods + 1` bars; the §3/§5 series-with-shift rows describe the interpreter's term model, not a YAML field.
- The `rsi` window bound 2..200 (§3) is enforced by the validator as `parameter_out_of_bounds` (review fix, 2026-10-07); `ema` keeps the common 1..500 window bound.
- §9 item 3 (RSI history dependence) is implemented through the real path, not only at the formula level: `tests/test_strategy_spec_parity.py::test_rsi_history_dependence_through_the_interpreter` parses two YAML documents differing only in `history` (50, 100), validates them, resolves each to a `DeclarativeDailyStrategy`, runs `generate_signals` over 780 sessions with bars served by the `bars_for_sessions` seam (the loader is asked for exactly 50 and 100 bars), and asserts values differ on more than 80 % of sessions, the direction differs on at least one, that date's 50-bar RSI sits on the other side of a threshold, and the `history: 100` run equals the original Python RSI strategy on every session and value. `tests/test_research_data_pipeline.py::test_rsi_history_dependence_through_approved_versions_and_the_database` repeats it on the database path: a `history: 50` version approved through the S2 service and the seeded `history: 100` example, both resolved by the research registry, with bars loaded by `bars_for_sessions` from a migrated database, compared with the original strategy reading the same rows. The explicit `history` parameter is therefore necessary, not decorative.
- The explanation names both numbers of a recursive term, for example "the 14-session RSI of the close, computed over 100 bars of history" (review fix, 2026-10-07: the earlier text printed the history as the window and omitted the RSI period).

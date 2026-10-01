# Memorandum — Fixes following the external review (Oct 2026)

Summary of everything fixed, verified, and documented following the quant
reviewer's analysis, applied to `btc_trend_squeeze_system.py`,
`paper_trading_squeeze_system.py`, and `live_trading_squeeze_system.py`.

Every point was verified against the actual code before touching anything —
no finding was accepted on the strength of its description alone, and where a
finding could not be confirmed, that is stated explicitly.

---

## 1. Volatility regime formula — flagged, not code-changed

**Finding:** `vol_pctl = (x[-1] <= x).mean()` selects the **high** percentile
of recent volatility (expansion), not the low one (compression), contrary to
what the name "squeeze" suggests. Verified with synthetic cases: the
quietest day in the window → percentile 1.00; the most volatile day →
percentile 0.20.

**Decision:** the formula is not modified. Changing it would select a
different set of days and invalidate the entire backtest/holdout already
validated. This is clearly documented in the header of all three files and
in the `compute_daily_regime()` docstring — "squeeze" is a legacy label, not
a literal description of what the filter does.

---

## 2. Silent data gap (reviewer's Priority 1)

**Finding:** a missing day in the 1-minute data propagates as NaN through the
180-day percentile, disabling the regime filter for weeks with no warning at
all — indistinguishable in the log from a normal period with no signal.

**Fix:**
- `compute_daily_regime()` reindexes onto a complete daily date range and
  emits an explicit warning (`CRITICAL WARNING`) if NaN appears in recent
  days.
- Tested with a synthetic case (1-day gap out of 300): no warning when data
  is complete, explicit warning when a real gap exists.

**Files:** all three.

---

## 3. Permanent download gap in live trading

**Finding:** `fetch_ohlcv_paginated` stopped on any empty batch from the
exchange (maintenance, rate-limit), and the next run resumed from the last
cached candle — the gap was never retried.

**Fix:** new `detect_and_refill_gaps()` function — detects real gaps (jumps
>1 minute in the index) and actively retries downloading that specific span
before returning the frame. Tested with a synthetic 10-minute gap, including
deliberate overlap with existing data: correctly filled, resulting index has
no remaining jumps.

**Files:** `paper_trading_squeeze_system.py`, `live_trading_squeeze_system.py`.

---

## 4. Stop sized off a price that doesn't exist yet

**Finding:** `stop_dist = max(MIN_STOP_PCT * price15[i], ...)` used the
15-minute candle's **close** — a value only known up to 14 minutes after the
actual entry occurs. This directly affected the size of every position (the
denominator of the sizing formula).

**Fix:** uses `level` (the breakout level, known in advance) instead of the
future close. Numerically verified the change is not cosmetic (a real ~0.3%
difference in the tested example).

**Files:** all three.

---

## 5. Ambiguous-candle veto with information leakage

**Finding:** `if break_up and break_down: continue` used the high/low of the
**entire** 15-minute candle (only fully known once it closes) to veto an
entry whose fill was backdated to an earlier minute within that same
candle — using future information for a decision already "made" earlier.

**Fix:** restructured to use the **minute-by-minute running** high/low
(causal). The minute at which each direction is confirmed is compared
separately; the signal is only discarded if both directions are confirmed at
the exact same minute (genuine ambiguity). Tested with 3 synthetic cases
(clean breakout, a breakout "rescued" that would previously have been vetoed
unnecessarily, and genuine ambiguity), plus a full integration test of the
engine over 400 synthetic days.

**Files:** all three.

---

## 6. TRAIN with an insufficient sample

**Finding:** TRAIN had only ~39-41 trades / 2 independent squeeze episodes,
yet was weighted in comparison tables as if it carried the same value as
VAL/HOLDOUT.

**Fix:**
- Extended the data download range further back (2022→2020) while keeping
  TRAIN's end date fixed (2022-12-31), using Binance's longer history. TRAIN
  grew to 422 trades / 7 independent episodes.
- Added an explicit warning in the backtest header about the real weight of
  each window.
- **Additional finding from the extension:** a -6.65% drawdown appeared in
  February 2021 (BTC's parabolic rally followed by a sharp correction) —
  more than double any drawdown seen in the 2022-2026 period. No trade
  exceeded its theoretical stop during that stretch; the episode containing
  it ended net positive. Pending human decision: whether the -8% circuit
  breaker is still the right level in light of this data point.

---

## 7. Inconsistent `>` vs `>=` operator

**Verified:** already resolved as a side effect of the fix in point 5 — the
15m filter and the minute-by-minute scan now use the same strict `>`
operator in all three files. No further change needed.

---

## 8. Re-entry counter, backtest vs. live

**Reviewed, finding not confirmed.** A direct test of the described scenario
was built (a position held open across midnight), comparing both pieces of
logic step by step: both reset the counter identically upon the first signal
of the new day. A subtler case not covered by this test cannot be ruled out.

---

## 9. `object` dtype trap in the regime data

**Finding:** `daily_regime.reindex(df15["day"]).values` forces `dtype=object`
by mixing float and bool columns; a NaN in that context reads as `True` in
boolean checks (`not NaN` is `False` in Python).

**Fix:** each column is now reindexed separately with its explicit dtype;
`was_squeeze` goes through `.fillna(False).astype(bool)`. Verified that the
gap fix (point 2) already blocked the specific exploitation path in
practice, but fixed defensively anyway, without relying on that particular
chain of computation. Tested by forcing a direct NaN into `was_squeeze`:
before the fix, `dtype=object` and it incorrectly read as squeeze active;
after the fix, `dtype=bool` and it correctly reads as squeeze inactive (the
safe side).

**Files:** all three.

---

## 10. Circuit breaker tripped by unrealized PnL

**Finding:** `peak_balance_btc` was updated every cycle using the account's
**total** balance (which on KuCoin COIN-M includes unrealized PnL from open
positions) — a floating gain could inflate the peak, and if that gain
shrank before the trade closed, the system could trip the -8% emergency
brake without having actually lost that much in realized terms.

**Fix:** the peak is now only updated while the account is **flat** (no open
position) — realized equity, not mark-to-market. No real protection is
lost: the circuit breaker is only ever evaluated right before looking for a
new entry, which only happens while flat anyway. Tested with a 4-cycle
scenario (a large floating gain that later shrinks, closing with a small
loss): the peak stayed untouched while the position was open, and the final
drawdown was correctly computed against the realized peak, not the floating
maximum.

**Files:** `live_trading_squeeze_system.py`.

---

## 11. Incorrect attribution across the IS/OOS/HOLDOUT windows

**Finding:** `period_stats` selected trades by `exit_time` but grouped daily
P&L by `day` (the **entry** day) — a trade that entered in one window and
closed in the next would pass the selection filter, but its result would be
lost when reindexing onto the new window's calendar.

**Fix:** now groups by the **exit** day, consistent with the selection
criterion. Tested with a synthetic case built specifically to trigger the
bug (a trade crossing the boundary with a 2% gain): the old version captured
only 1% of the 3% total; the new version captures the full 3%. Confirmed
that, with current data (median hold: 9 minutes), the bug remained inactive
in practice (0 of 1057 trades cross any boundary) — fixed preemptively, as
the reviewer requested.

**Files:** `btc_trend_squeeze_system.py`.

---

## 12. Three instruments, one parameter set

**Unresolved — accepted and documented risk, not a code bug.** The backtest
is validated on spot data (Bitstamp/Binance); paper trades KuCoin USDT-M
(linear perpetual); live trades KuCoin COIN-M (inverse perpetual). Each
system computes its own Donchian channel from its own exchange's data — no
levels are "imported" across instruments — but the system has never been
historically validated specifically on perpetual-contract data. Perpetuals
have dynamics (funding, liquidation-cascade wicks) that spot does not.
Explicitly documented in the header of `live_trading_squeeze_system.py`.
Pending: obtaining KuCoin Futures historical data to validate directly.

---

## 13. Unit mismatch in the delivered CSV

**Finding:** `trades_squeeze_system.csv` stored `stop_pct` as 0.501
(percentage points), the same name as the internal parameter expected by
`simulate_equity_with_risk_layer`, which expects a fraction (0.00501). Real
risk of ~100x-wrong sizing if the CSV were reloaded without converting
units.

**Fix:** column renamed to `stop_dist_pct_valor_ya_en_porcentaje` —
deliberately explicit so there is no room for ambiguity about units.

**File:** `trades_squeeze_system.csv`.

---

## Summary — what actually changed the results

| Point | Did it change the numbers? |
|---|---|
| Silent data gap | Not directly — prevents future failures |
| Permanent download gap | Not directly — prevents future failures |
| Stop with future price | Yes — changes the sizing of every trade |
| Ambiguous-candle veto | Yes — rescues entries that were previously discarded unnecessarily |
| Extended TRAIN | Yes — adds ~3 years of history and reveals the -6.65% drawdown from 2021 |
| `object` dtypes | Not in current practice — defensive |
| Circuit breaker with unrealized PnL | Not in the backtest — only affects live behavior |
| Split attribution | Not in current practice — defensive |
| CSV units | No — documentation only |

**Net result, full backtest 2020-2026 (Binance, all fixes applied):** Total
Sharpe 3.47, CAGR 85.0%, MaxDD -6.7% (previously unknown, now confirmed as
the worst real scenario observed), 1057 trades. The edge survives every
correction — none of the more than ten audit rounds this strategy has gone
through, across its entire history, has made the system stop working once a
real bias was fixed.

**Pending human decision:** whether the -8% circuit breaker is still the
right level now that we know the system can reach -6.7% in a real, not
hypothetical, extreme market.

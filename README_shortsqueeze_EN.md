# BTC Volatility Squeeze Breakout — README

Directional breakout system on BTC, filtered by prior volatility compression
and confirmed by volume, with intraday re-entry. Validated with strict
train/val/holdout discipline and audited against lookahead bias, intrabar
execution ambiguity, and slippage sensitivity.

Code: `btc_trend_squeeze_system.py` (backtest) — `paper_trading_squeeze_system.py`
(live version, same exact signal logic, kept in sync).

---

## 1. How the features/indicators are built

All indicators use an explicit `shift(1)` — no value used in a given
period's decision includes information from that same period.

**Daily regime (recalculated every day, UTC):**

| Indicator | Calculation | Window |
|---|---|---|
| Donchian channel | `max(high)` / `min(low)` of prior days | 5 days |
| Realized volatility | `std(daily log-returns) × √365` | 20 days |
| Volatility percentile | Percentile of the 20d vol. within its own history | 180 days |
| Squeeze active | `True` if the percentile was below 30% on any of the last N days | 14 days |

**15-minute context (for execution):**

| Indicator | Calculation | Window |
|---|---|---|
| ATR | Mean True Range | 24h (96 x 15m bars), `shift(1)` |
| Average volume | Mean volume per bar | 20 x 15m bars, `shift(1)` |

The daily regime is mapped onto each 15m bar by date (`day = index.normalize()`).

---

## 2. Exact entry and exit logic

**Entry condition (all must hold):**
1. Squeeze active (see above)
2. The 15m candle breaks the Donchian channel (above → long, below → short)
3. Discarded if the candle breaks **both** directions at once (ambiguous signal)
4. **Causal volume confirmation**: within the 15m candle where the breakout
   occurs, the system scans minute by minute. Accumulated volume is compared
   against a pro-rated threshold (1.5× the 20-bar average), but **counting
   only minutes strictly prior to the exact crossing minute** — never the
   volume of the current minute itself (that would not be causal: at the
   exact instant of the cross, that volume doesn't exist yet)

**Execution:** the theoretical fill is the exact Donchian channel level, at
the minute the price crosses it (1-minute resolution, not 15).

**Intraday re-entry:** up to 3 attempts per calendar day (the original + 2
re-entries) while the squeeze remains active. Explicitly tested against
alternatives (cap per squeeze episode instead of per day, no cap at all) —
none improved on the calendar-day version, so it was kept as is.

**Exit:** dynamic (trailing) stop only — no take-profit, no time-based close.
The position runs until the stop closes it.

---

## 3. SL/TP management

**No take-profit.** Only a dynamic (trailing) stop-loss.

**Initial stop:**
```
stop_distance = max(0.5% of entry price, 0.5 × 24h ATR)
```
In practice the 0.5% floor dominates in most trades.

**Trailing — conservative convention:** on every 1-minute bar after entry, the
system **checks first** whether price has already hit the current stop;
only if it hasn't, the stop is updated in favor of the position using that
bar's high/low. (The opposite convention — updating first and checking
afterward — is optimistic and was discarded after the audit.)

**Special case — stop hit within the entry candle itself:** if, within the
same entry minute, price also touches the stop level, the position is
closed right there (it doesn't wait for the next bar). Affects ~2.6% of
entries.

**Position sizing (risk layer, separate from the signal):**
```
position_size = (target_risk × equity) / stop_distance_%
```
- Target risk: 1.0% of equity per trade (funded phase) / 0.75% (challenge)
- Maximum leverage: 5x
- Staged de-risking: 5-8% drawdown → risk halved; >8% drawdown → new
  entries halted (circuit breaker)

---

## 4. Commission and slippage treatment

**Fixed commission in the backtest:** 5 bps per side, applied on both entry
and exit (10 bps total round-trip cost).

**Slippage: NONE is assumed on top of the backtest's reference number** — the
theoretical fill is the exact Donchian channel level. This is a known,
explicitly audited limitation, not an oversight:

| Scenario | Capital multiple (validation period) |
|---|---|
| Exact fill + 5bps/side (backtest reference) | ~8.5x |
| + additional fixed slippage 5-10bps | 4.4x – 6.1x |
| **Break-even point** | **~15-25bps additional per side** |
| + 30bps+ additional slippage | Loss |

In other words: **the system loses its edge if real effective slippage
exceeds ~20-30bps per side.** This is being actively measured in
production — the paper trading version captures the real market price at
the exact instant of each signal (`real_price_at_entry`, `slippage_entry_pct`
in the trade log) to compare against the backtest's theoretical fill.

A validation using real Binance tick data (10 events, 2025) showed high
liquidity (millisecond gaps between trades) and 5-second slippage between
-0.075% and +0.059% — within the tolerable margin, but with an
insufficient sample to be conclusive.

---

## 5. Train / Validation / Holdout

**Fixed temporal split, no walk-forward:**

| Window | Period |
|---|---|
| TRAIN (IS) | Start of data → 2022-12-31 |
| VAL (OOS) | 2023-01-01 → 2024-12-31 |
| HOLDOUT | 2025-01-01 → end of data |

The holdout was touched exactly once per design change already confirmed on
TRAIN/VAL (no parameter was optimized by looking at the holdout). An
explicit record was kept of how many times the holdout was consulted
throughout development (risk of *winner's curse* from multiple comparisons)
— real statistical independence was verified by grouping trades by squeeze
episode rather than by individual trade (~17 independent episodes across 4
years of data, not hundreds of independent trades).

**There is no formal walk-forward** (periodic parameter re-fitting) —
parameters are fixed from the initial design and remain constant across the
whole period. This remains an open item, not implemented.

---

## Warnings to read before allocating capital

The script itself (`btc_trend_squeeze_system.py`) documents, in its header,
a 6-round audit history of the real bugs found and fixed (lookahead in the
regime signal, 15m execution ambiguity resolved by moving to 1m, volume
causality, etc.). **The most important open point is real slippage** —
everything else has been verified; this has not, yet.

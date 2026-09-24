"""
ATR BREAKOUT MNQ — ESTRATEGIA COMPLETA v2.2
=============================================


CAMBIO ÚNICO respecto a v2.1:
  · FILTRO RANGO DE BREAKOUT — rango de la barra ≤ 0.50 × ATR(14)
    Barras de rango muy amplio en el slot de breakout predicen
    trades peores (Spearman r=-0.167, p=0.000).
    Una barra >0.5×ATR en 5-min indica volatilidad de ruido
    destinada a revertirse, no momentum limpio.
    Plateau robusto: 0.40–0.80×ATR → no es pico optimizado.

SIN CAMBIOS respecto a v2.1:
  · Stop: session open (igual que v2.1)
  · Máximo trades/día: ilimitado (igual que v2.1)
  · Swing stop: desactivado (igual que v2.1)
  · Todos los demás parámetros idénticos


RESULTADOS VALIDADOS:
  IS  2019-2021:    Sharpe 2.099  (+0.065 vs v2.1)
  OOS 2022-2025:    Sharpe 1.720  (+0.054 vs v2.1)
  Holdout 2026:     Sharpe 0.982  (=0.000 vs v2.1 — neutro)
  Walk-forward media OOS: 2.522   (+0.045 vs v2.1 baseline 2.477)

USO:
    python atr_breakout_v2_2.py --data archivo.parquet
    python atr_breakout_v2_2.py --data archivo.parquet --compare-v2111
    python atr_breakout_v2_2.py --data archivo.parquet --capital 30000
"""

from __future__ import annotations
import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN — todos los parámetros del sistema
# ═══════════════════════════════════════════════════════════════
@dataclass
class Config:
    # Señal
    atr_period:     int   = 14
    band_mult:      float = 0.50
    exec_slots:     frozenset = frozenset({0, 15, 30, 45})

    # NUEVO v3: filtro rango barra breakout
    # Solo generar señal si (high-low) / ATR <= max_brk_range_atr
    # Valor 0.0 = desactivado (comportamiento v2.1)
    max_brk_range_atr: float = 0.50  # v2.2: activo

    # NUEVO v3: stop swing
    # Número de barras de 5-min para calcular el swing low/high
    # 0 = desactivado (usa session open como en v2.1)
    swing_stop_bars:   int   = 0    # v2.2: desactivado (usa SO)

    # NUEVO v3: máximo trades por día
    # Con swing stop el sistema puede re-entrar tras un stop.
    # Limitar evita sobreoperación en días caóticos con 5+ trades
    # que el análisis muestra que son sistemáticamente negativos.
    max_trades_day:    int   = 99   # v2.2: ilimitado (igual v2.1)

    # Volatility targeting
    vol_target:     float = 0.030
    vol_slow:       int   = 14
    vol_fast:       int   = 5
    scale_min:      float = 0.50
    scale_max:      float = 1.50

    # Position sizing
    n_min:          int   = 1
    n_max:          int   = 20

    # Drawdown scaling
    dd_start:       float = 0.05
    dd_floor:       float = 0.75

    # Circuit breaker
    monthly_cb:     float = -0.08

    # Horarios (ET)
    eod_hour:       int   = 15
    eod_min:        int   = 30

    # Instrumento (MNQ)
    point_value:    float = 2.0
    commission:     float = 0.62
    slippage:       float = 0.25

    # Filtro ATR mínimo (v2.1, sin cambio)
    min_atr_pct:    float = 0.01

    # Capital
    initial_capital: float = 20_000.0


# ═══════════════════════════════════════════════════════════════
# ESTADO DE TRADING
# ═══════════════════════════════════════════════════════════════
@dataclass
class State:
    equity:        float
    peak:          float
    position:      int   = 0
    entry_price:   Optional[float] = None
    entry_time:    Optional[pd.Timestamp] = None
    stop_level:    Optional[float] = None   # NUEVO v3: nivel de stop actual
    pending_dir:   int   = 0
    wait_bars:     int   = 0
    near_stop:     bool  = False
    day_trades:    int   = 0               # NUEVO v3: trades en el día actual
    month_key:     str   = ""
    month_start:   float = 0.0
    month_paused:  bool  = False


# ═══════════════════════════════════════════════════════════════
# INDICADORES DIARIOS (sin lookahead — shift(1))
# ═══════════════════════════════════════════════════════════════
def build_daily_indicators(df_1min: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    daily = df_1min.groupby(df_1min.index.normalize()).agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"),     close=("close", "last")
    )
    daily.index = pd.to_datetime(daily.index)

    c = daily["close"].values
    h = daily["high"].values
    l = daily["low"].values
    prev_c = c[:-1]

    tr  = np.maximum(h[1:] - l[1:],
          np.maximum(np.abs(h[1:] - prev_c), np.abs(l[1:] - prev_c)))
    atr = pd.Series(tr, index=daily.index[1:]).rolling(cfg.atr_period).mean()

    log_ret = pd.Series(np.diff(np.log(c)), index=daily.index[1:])
    v14 = log_ret.rolling(cfg.vol_slow).std()
    v5  = log_ret.rolling(cfg.vol_fast, min_periods=3).std()

    return pd.DataFrame({
        "sess_open": daily["open"],
        "atr14":     atr.shift(1),
        "vol14":     v14.shift(1),
        "vol5":      v5.shift(1),
    }, index=daily.index)


# ═══════════════════════════════════════════════════════════════
# POSITION SIZING
# ═══════════════════════════════════════════════════════════════
def compute_n_contracts(equity: float, peak: float, sess_open: float,
                        vol14: float, vol5: float, cfg: Config) -> int:
    PRICE_REF = 12_000.0
    price_adj = sess_open / PRICE_REF

    if not pd.isna(vol5) and vol5 > 0:
        scale = float(np.clip(vol14 / vol5, cfg.scale_min, cfg.scale_max))
    else:
        scale = 1.0
    vol_tgt = cfg.vol_target * price_adj * scale

    if peak > 0:
        dd = (equity - peak) / peak
        if dd < 0:
            beyond   = max(0.0, -dd - cfg.dd_start)
            dd_range = 0.20 - cfg.dd_start
            mult     = max(cfg.dd_floor,
                           1.0 - (beyond / dd_range) * (1.0 - cfg.dd_floor))
            vol_tgt *= mult

    sigma = vol14 * sess_open * cfg.point_value
    if sigma <= 0:
        return cfg.n_min

    n = round((vol_tgt * equity) / sigma)
    return int(np.clip(n, cfg.n_min, cfg.n_max))


# ═══════════════════════════════════════════════════════════════
# CIRCUIT BREAKER MENSUAL
# ═══════════════════════════════════════════════════════════════
def check_circuit_breaker(state: State, day: pd.Timestamp,
                           cfg: Config) -> bool:
    mk = day.strftime("%Y-%m")
    if mk != state.month_key:
        state.month_key    = mk
        state.month_start  = state.equity
        state.month_paused = False

    if state.month_start > 0:
        dd_mes = (state.equity - state.month_start) / state.month_start
        if dd_mes < cfg.monthly_cb and not state.month_paused:
            state.month_paused = True

    return state.month_paused


# ═══════════════════════════════════════════════════════════════
# LÓGICA DE TRADING — una barra de 5-min a la vez
# ═══════════════════════════════════════════════════════════════
def process_bar(state: State, cfg: Config, bar: pd.Series,
                ts: pd.Timestamp, day_ctx: dict, is_last_bar: bool,
                trades: list,
                # NUEVO v3: buffers de swing (listas mutables)
                swing_lows: list, swing_highs: list) -> None:
    """
    Procesa una sola barra de 5-min.
    Modifica state in-place y añade trades al log si hay cierres.

    Cambios v3 vs v2.1:
    · stop_level: puede ser SO (v2.1) o swing low/high (v3)
    · swing_lows/swing_highs: buffer rolling de N barras para stop
    · day_trades: contador de trades del día (máx 3 en v3)
    · Filtro rango: breakout ignorado si rango > 0.50×ATR
    """
    so    = day_ctx["sess_open"]
    n     = day_ctx["n_contracts"]
    upper = day_ctx["band_upper"]
    lower = day_ctx["band_lower"]
    atr14 = day_ctx["atr14"]

    is_slot  = ts.minute in cfg.exec_slots
    is_eod   = ts.hour == cfg.eod_hour and ts.minute >= cfg.eod_min
    bar_ret5 = bar.get("ret5", np.nan)

    # ── Actualizar buffer de swing ANTES de cualquier decisión ──
    # Causalmente correcto: el low/high de la barra actual se conoce
    # al cierre de esa barra, que es el mismo instante que la entrada.
    swing_lows.append(bar["low"])
    swing_highs.append(bar["high"])
    if len(swing_lows)  > cfg.swing_stop_bars: swing_lows.pop(0)
    if len(swing_highs) > cfg.swing_stop_bars: swing_highs.pop(0)

    # ── HELPER: cerrar posición ──────────────────────────────────
    def close_position(exit_price: float, reason: str) -> None:
        pnl = (exit_price - state.entry_price) * state.position \
              * cfg.point_value * n - cfg.commission * n
        state.equity += pnl
        if state.equity > state.peak:
            state.peak = state.equity

        trades.append({
            "date":       ts.normalize(),
            "entry_time": state.entry_time.strftime("%H:%M") if state.entry_time else "",
            "exit_time":  ts.strftime("%H:%M"),
            "direction":  "LONG" if state.position == 1 else "SHORT",
            "entry":      round(state.entry_price, 2),
            "exit":       round(exit_price, 2),
            "so":         round(so, 2),
            "stop_level": round(state.stop_level, 2) if state.stop_level else so,
            "band_upper": round(upper, 2),
            "band_lower": round(lower, 2),
            "atr14":      round(atr14, 2),
            "n":          n,
            "pnl":        round(pnl, 2),
            "equity":     round(state.equity, 2),
            "reason":     reason,
        })
        state.position    = 0
        state.entry_price = None
        state.entry_time  = None
        state.stop_level  = None
        state.near_stop   = False
        state.pending_dir = 0
        state.wait_bars   = 0

    # ═══════════════════════════════════════════════════════════
    # CASO 1 — POSICIÓN ABIERTA
    # ═══════════════════════════════════════════════════════════
    if state.position != 0:
        pos = state.position
        sl  = state.stop_level  # nivel de stop (swing o SO)

        if is_eod or is_last_bar:
            xp = bar["close"] - cfg.slippage * pos
            close_position(xp, "EOD")
            return

        # Detectar toque del stop
        hit_stop = ((pos == 1  and bar["low"]  <= sl) or
                    (pos == -1 and bar["high"] >= sl))
        if hit_stop and not state.near_stop:
            state.near_stop = True

        if state.near_stop:
            favorable  = (not pd.isna(bar_ret5) and
                          ((pos == 1  and bar_ret5 > 0) or
                           (pos == -1 and bar_ret5 < 0)))
            definitive = ((pos == 1  and bar["close"] < sl) or
                          (pos == -1 and bar["close"] > sl))

            if favorable or definitive:
                xp = sl if definitive else bar["close"]
                xp -= cfg.slippage * pos
                close_position(xp, "STOP")
        return

    # ═══════════════════════════════════════════════════════════
    # CASO 2 — SEÑAL PENDIENTE (esperando pullback)
    # ═══════════════════════════════════════════════════════════
    if state.pending_dir != 0:
        pend = state.pending_dir
        state.wait_bars += 1

        pullback = (not pd.isna(bar_ret5) and
                    ((pend == 1  and bar_ret5 < 0) or
                     (pend == -1 and bar_ret5 > 0)))

        if pullback or state.wait_bars >= 99 or is_last_bar:
            if not is_last_bar and state.day_trades < cfg.max_trades_day:
                # ── NUEVO v3: calcular stop swing ───────────────
                if cfg.swing_stop_bars > 0:
                    if pend == 1:
                        sl = min(swing_lows)  - cfg.slippage
                    else:
                        sl = max(swing_highs) + cfg.slippage
                else:
                    sl = so  # fallback: session open (comportamiento v2.1)

                state.position    = pend
                state.entry_price = bar["close"] + cfg.slippage * pend
                state.entry_time  = ts
                state.stop_level  = sl
                state.day_trades += 1

            state.pending_dir = 0
            state.wait_bars   = 0
        return

    # ═══════════════════════════════════════════════════════════
    # CASO 3 — FLAT, buscar breakout en slot :00/:15/:30/:45
    # ═══════════════════════════════════════════════════════════
    if is_slot and not is_eod:
        # ── NUEVO v3: filtro de rango de la barra de breakout ───
        # Si la barra tiene un rango intrabar muy amplio (>0.50×ATR)
        # es señal de volatilidad de ruido — ignorar el breakout.
        if cfg.max_brk_range_atr > 0 and atr14 > 0:
            bar_range = bar["high"] - bar["low"]
            if bar_range / atr14 > cfg.max_brk_range_atr:
                return  # barra de rango excesivo — no generar señal

        if bar["close"] > upper:
            state.pending_dir = 1
            state.wait_bars   = 0
        elif bar["close"] < lower:
            state.pending_dir = -1
            state.wait_bars   = 0


# ═══════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════
def run_backtest(df_1min: pd.DataFrame,
                 cfg: Config) -> tuple[pd.DataFrame, float]:
    ind = build_daily_indicators(df_1min, cfg)

    bars5 = (df_1min.resample("5min", closed="left", label="left")
             .agg(open=("open", "first"), high=("high", "max"),
                  low=("low", "min"),     close=("close", "last"))
             .dropna()
             .between_time("09:30", "15:29"))
    bars5["date"] = bars5.index.normalize()
    bars5["ret5"] = bars5.groupby("date")["close"].pct_change()
    bars = bars5.join(ind, on="date").dropna(subset=["atr14", "vol14"])

    state  = State(equity=cfg.initial_capital, peak=cfg.initial_capital)
    trades = []

    for day in sorted(bars["date"].unique()):
        day_bars = bars[bars["date"] == day].sort_index()
        if len(day_bars) < 5:
            continue

        if check_circuit_breaker(state, pd.Timestamp(day), cfg):
            continue

        so    = day_bars["sess_open"].iloc[0]
        vol14 = day_bars["vol14"].iloc[0]
        vol5  = day_bars["vol5"].iloc[0]
        atr14 = day_bars["atr14"].iloc[0]

        if pd.isna(vol14) or vol14 <= 0 or pd.isna(atr14):
            continue

        # Filtro ATR mínimo (v2.1, sin cambio)
        if cfg.min_atr_pct > 0 and so > 0:
            if atr14 / so < cfg.min_atr_pct:
                continue

        n = compute_n_contracts(state.equity, state.peak, so,
                                vol14, vol5, cfg)

        day_ctx = {
            "sess_open":   so,
            "atr14":       atr14,
            "vol14":       vol14,
            "n_contracts": n,
            "band_upper":  so + cfg.band_mult * atr14,
            "band_lower":  so - cfg.band_mult * atr14,
        }

        # ── NUEVO v3: reset contador de trades y buffers de swing
        state.day_trades = 0
        swing_lows:  list = []
        swing_highs: list = []

        for i, (ts, bar) in enumerate(day_bars.iterrows()):
            is_last = (i == len(day_bars) - 1)
            process_bar(state, cfg, bar, ts, day_ctx,
                        is_last, trades, swing_lows, swing_highs)

    return pd.DataFrame(trades), state.equity


# ═══════════════════════════════════════════════════════════════
# MÉTRICAS
# ═══════════════════════════════════════════════════════════════
def compute_metrics(trades: pd.DataFrame, eq_final: float,
                    cfg: Config) -> dict:
    if len(trades) < 5:
        return {}

    cap   = cfg.initial_capital
    years = max((trades["date"].max() - trades["date"].min()).days / 365.25, 0.1)
    cagr  = (eq_final / cap) ** (1 / years) - 1

    daily_pnl = trades.groupby("date")["pnl"].sum()
    sharpe    = (daily_pnl.mean() / daily_pnl.std()) * np.sqrt(252) \
                if daily_pnl.std() > 0 else 0
    neg_pnl   = daily_pnl[daily_pnl < 0]
    sortino   = (daily_pnl.mean() / neg_pnl.std()) * np.sqrt(252) \
                if len(neg_pnl) > 0 else 0

    eq_curve  = trades["equity"]
    mdd       = ((eq_curve - eq_curve.cummax()) / eq_curve.cummax()).min()

    wins      = trades["pnl"] > 0
    pf        = (trades[wins]["pnl"].sum() / abs(trades[~wins]["pnl"].sum())
                 if (~wins).any() else 99.0)

    by_year   = {}
    for yr, grp in trades.groupby(trades["date"].dt.year):
        cap0 = grp["equity"].iloc[0] - grp["pnl"].iloc[0]
        by_year[int(yr)] = (grp["equity"].iloc[-1] / cap0 - 1) * 100

    # Desglose por razón de salida
    eod_trades  = trades[trades["reason"] == "EOD"]
    stop_trades = trades[trades["reason"] == "STOP"]

    return {
        "cagr":       cagr,
        "sharpe":     sharpe,
        "sortino":    sortino,
        "mdd":        mdd,
        "pf":         pf,
        "wr":         wins.mean(),
        "exp":        trades["pnl"].mean(),
        "trades":     len(trades),
        "days":       len(daily_pnl),
        "eq_final":   eq_final,
        "by_year":    by_year,
        "eod_n":      len(eod_trades),
        "eod_wr":     (eod_trades["pnl"] > 0).mean() if len(eod_trades) > 0 else 0,
        "stop_n":     len(stop_trades),
        "stop_exp":   stop_trades["pnl"].mean() if len(stop_trades) > 0 else 0,
    }


# ═══════════════════════════════════════════════════════════════
# CARGA DE DATOS
# ═══════════════════════════════════════════════════════════════
def load_data(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    if df.index.tzinfo is not None:
        df.index = df.index.tz_convert("America/New_York").tz_localize(None)
    df = df.between_time("09:30", "15:59")
    df = df[df.index.dayofweek < 5]
    return df[["open", "high", "low", "close", "volume"]]


# ═══════════════════════════════════════════════════════════════
# REPORTE
# ═══════════════════════════════════════════════════════════════
def print_report(metrics: dict, cfg: Config, label: str = "v3.0") -> None:
    if not metrics:
        print("Sin trades suficientes para reporte."); return

    print()
    print("═" * 58)
    print(f"  ATR BREAKOUT MNQ {label} — RESULTADOS")
    print("═" * 58)
    print(f"  Capital inicial:    ${cfg.initial_capital:>12,.0f}")
    print(f"  Capital final:      ${metrics['eq_final']:>12,.0f}")
    print(f"  Profit total:       ${metrics['eq_final']-cfg.initial_capital:>+12,.0f}")
    print()
    print(f"  CAGR:               {metrics['cagr']*100:>+11.2f}%")
    print(f"  Sharpe:             {metrics['sharpe']:>12.3f}")
    print(f"  Sortino:            {metrics['sortino']:>12.3f}")
    print(f"  MDD:                {metrics['mdd']*100:>11.2f}%")
    print(f"  Profit Factor:      {metrics['pf']:>12.3f}")
    print(f"  Win Rate:           {metrics['wr']*100:>11.2f}%")
    print(f"  Expectancy:         ${metrics['exp']:>+12.2f}/trade")
    print(f"  Trades:             {metrics['trades']:>12}")
    print()
    print(f"  Salidas EOD:        {metrics['eod_n']:>12}  WR {metrics['eod_wr']*100:.1f}%")
    print(f"  Salidas STOP:       {metrics['stop_n']:>12}  Exp ${metrics['stop_exp']:>+.0f}")
    print()
    print(f"  Stop mode:          {'Session Open (SO)' if cfg.swing_stop_bars==0 else 'Swing '+str(cfg.swing_stop_bars)+'b, máx '+str(cfg.max_trades_day)+'/día':>20}")
    print(f"  Filtro rango:       {cfg.max_brk_range_atr:>11.2f}×ATR")
    print(f"  Filtro ATR mín:     {cfg.min_atr_pct*100:>10.1f}%")

    if metrics.get("by_year"):
        print()
        print("  Por año:")
        for yr, ret in sorted(metrics["by_year"].items()):
            sign = "+" if ret >= 0 else ""
            print(f"    {yr}:            {sign}{ret:>8.2f}%")
    print("═" * 58)


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="ATR Breakout MNQ v3.0 — backtest")
    parser.add_argument("--data",       type=str,
        default="mnq_1min_2019_2024.parquet")
    parser.add_argument("--capital",    type=float, default=20_000.0)
    parser.add_argument("--output",     type=str,   default=None)
    parser.add_argument("--compare-v211", action="store_true",
        help="Ejecutar también el baseline v2.1 y comparar")
    args = parser.parse_args()

    path = Path(args.data)
    if not path.exists():
        print(f"ERROR: archivo no encontrado: {path}"); sys.exit(1)

    print(f"Cargando datos: {args.data}")
    df = load_data(args.data)
    print(f"  Barras 1-min: {len(df):,}")
    print(f"  Período: {df.index.min().date()} → {df.index.max().date()}")

    # ── Backtest v3 ──────────────────────────────────────────────
    cfg_v3 = Config(initial_capital=args.capital)
    print("\nEjecutando backtest v3.0...")
    trades_v3, eq_v3 = run_backtest(df, cfg_v3)
    metrics_v3 = compute_metrics(trades_v3, eq_v3, cfg_v3)
    print_report(metrics_v3, cfg_v3, "v2.2")

    # ── Comparación con v2.1 (opcional) ─────────────────────────
    if args.compare_v21:
        cfg_v2 = Config(
            initial_capital=args.capital,
            swing_stop_bars=0,          # v2.1: SO stop
            max_brk_range_atr=0.0,      # v2.1: sin filtro rango
            max_trades_day=99,          # v2.1: ilimitado
        )
        print("\nEjecutando backtest v2.1 (baseline)...")
        trades_v2, eq_v2 = run_backtest(df, cfg_v2)
        metrics_v2 = compute_metrics(trades_v2, eq_v2, cfg_v2)
        print_report(metrics_v2, cfg_v2, "v2.1 baseline")

        # Tabla comparativa
        print()
        print("═" * 52)
        print("  COMPARATIVA v2.1 → v2.2")
        print("═" * 52)
        for k, label in [
            ("cagr",    "CAGR"),
            ("sharpe",  "Sharpe"),
            ("sortino", "Sortino"),
            ("mdd",     "MDD"),
            ("pf",      "Profit Factor"),
            ("wr",      "Win Rate"),
            ("trades",  "Trades"),
        ]:
            v2 = metrics_v2.get(k, 0)
            v3 = metrics_v3.get(k, 0)
            if k in ("cagr", "mdd", "wr"):
                print(f"  {label:<16} {v2*100:>+8.2f}%  →  {v3*100:>+8.2f}%"
                      f"  ({(v3-v2)*100:>+.2f}pp)")
            elif k == "trades":
                print(f"  {label:<16} {int(v2):>9}  →  {int(v3):>9}"
                      f"  ({int(v3-v2):>+})")
            else:
                print(f"  {label:<16} {v2:>9.3f}  →  {v3:>9.3f}"
                      f"  ({v3-v2:>+.3f})")
        print("═" * 52)

    # Guardar trades
    if args.output and len(trades_v3) > 0:
        out = trades_v3.copy()
        out["date"] = out["date"].dt.strftime("%Y-%m-%d")
        out.to_csv(args.output, index=False)
        print(f"\nTrades guardados en: {args.output}")


if __name__ == "__main__":
    main()

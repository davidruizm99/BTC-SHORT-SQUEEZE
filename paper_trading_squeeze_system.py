"""
================================================================================
BTC VOLATILITY SQUEEZE BREAKOUT — PAPER TRADING EN VIVO
================================================================================
Ejecuta el mismo sistema validado en el backtest (Donchian(5) + squeeze +
volumen confirmado + reentrada + ejecucion a 1 minuto) contra datos REALES
en tiempo real, en modo PAPER (simulado, sin dinero real, sin colocar
ordenes en el exchange).

IMPORTANTE -- LEER ANTES DE USAR:
    - Este script NO se ha podido probar contra datos en vivo (el entorno
      donde se escribio no tiene acceso a APIs de exchanges). Probadlo con
      cuidado, revisando los logs, antes de confiar en el sin supervision.
    - Es SOLO paper trading -- no coloca ordenes reales. Para pasar a real
      hay que añadir la logica de ordenes con las claves API del exchange,
      y NO se incluye aqui a proposito (mejor añadirla vosotros cuando
      esteis seguros del comportamiento en paper).
    - Recordad la advertencia del backtest: el edge real depende
      criticamente de conseguir un slippage efectivo por debajo de
      ~20-30bps por lado. Este script en modo paper OS PERMITE MEDIR ESO
      exactamente -- comparad el precio de "fill teorico" que registra
      (nivel de Donchian) contra el precio real de mercado en ese instante,
      para estimar el slippage que tendriais en real.
    - VALIDACION CON DATOS DE TICK (10 eventos reales de Binance, marzo-
      noviembre 2025): la liquidez real es muy alta (gaps entre trades de
      milisegundos en la mayoria de los casos), y el movimiento de precio
      a 5 segundos del instante de referencia fue de -0.075% a +0.059% en
      la muestra -- comodamente dentro del margen de 20-30bps. Pero
      tambien hubo un minuto con 22.923 trades y 0.96% de rango de precio
      en 60 segundos (29-oct-2025) -- la ambiguedad de la vela de 1 minuto
      es real en momentos de alta volatilidad. Con solo 10 eventos no es
      una muestra concluyente -- seguid registrando el slippage real que
      seais consiguiendo en paper para tener una estimacion propia mejor.

SINCRONIZACION CON EL BACKTEST:
    Esta version incluye el fix de la RONDA 6 del backtest (confirmacion
    de volumen estrictamente causal -- ver check_for_new_entry: el volumen
    usado para confirmar la ruptura solo cuenta minutos ANTERIORES al
    minuto donde el precio cruza el nivel, nunca el propio minuto del
    cruce). Si el backtest recibe mas correcciones en el futuro, recordad
    aplicarlas tambien aqui -- la logica de señal debe ser identica en
    los dos sitios o los resultados dejan de ser comparables.

QUE HACE
--------
1. Descarga velas de 1 minuto de KuCoin (via ccxt) y mantiene una cache
   local en parquet para no tener que re-descargar todo el historico en
   cada ejecucion.
2. Calcula el regimen diario (Donchian(5), squeeze de volatilidad) con la
   misma logica exacta que el backtest -- shift(1) para evitar lookahead.
3. Si esta plano (sin posicion abierta) y hay señal valida (ruptura +
   squeeze activo + volumen confirmado en tiempo real), abre una posicion
   PAPER: registra el "fill teorico" (nivel de Donchian) para poder medir
   despues cuanto se habria desviado un fill real.
4. Si hay una posicion abierta, comprueba el stop dinamico (trailing,
   convencion conservadora: comprobar antes de actualizar) en cada vela
   de 1 minuto nueva.
5. Guarda el estado (posicion abierta, contador de reentradas del dia,
   equity) en un archivo JSON, para que el script pueda pararse y
   reanudarse sin perder informacion.
6. Registra cada trade cerrado en un CSV.

COMO USARLO
-----------
    pip install ccxt pandas numpy pyarrow --break-system-packages

    Ejecucion puntual (comprueba una vez y sale) -- pensado para lanzar
    con cron cada minuto:
        python paper_trading_squeeze_system.py --once

    Ejecucion continua (bucle propio, comprueba cada 60s):
        python paper_trading_squeeze_system.py --loop

    Por defecto usa KuCoin Futures (BTC/USDT perpetuo). Para usar spot,
    cambiar EXCHANGE_ID y SYMBOL abajo.
================================================================================
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import ccxt
except ImportError:
    sys.exit("Falta ccxt. Instalar con: pip install ccxt --break-system-packages")

SCRIPT_DIR = Path(__file__).resolve().parent

# ─── Configuracion del exchange ──────────────────────────────────────────────
EXCHANGE_ID = "kucoinfutures"   # cambiar a "kucoin" para spot
SYMBOL = "BTC/USDT:USDT"        # simbolo del perpetuo en KuCoin Futures via ccxt
                                  # para spot seria simplemente "BTC/USDT"

# ─── Parametros del sistema (identicos al backtest validado) ────────────────
DONCHIAN_DAYS = 5
VOL_WINDOW_DAYS = 20
VOL_PCTL_LOOKBACK_DAYS = 180
SQUEEZE_PCTL_TH = 0.30
SQUEEZE_LOOKBACK_DAYS = 14
ATR_HOURS = 24
ATR_MULT = 0.5
MIN_STOP_PCT = 0.005
VOL_CONFIRM_MULT = 1.5
VOL_CONFIRM_WINDOW = 20          # en barras de 15m
MAX_ENTRIES_PER_DAY = 3
COST_BPS = 5                     # coste ESTIMADO por lado, solo para registro -- en real
                                   # sustituir por el coste/slippage realmente observado

# ─── Gestion de riesgo ────────────────────────────────────────────────────────
RISK_PCT_FUNDED = 0.0100
RISK_PCT_CHALLENGE = 0.0075
CURRENT_PHASE = "funded"         # cambiar a "challenge" si aplica
MAX_LEVERAGE = 5.0
DERISK_DD_THRESHOLD = 0.05
HALT_DD_THRESHOLD = 0.08
INITIAL_EQUITY = 1.0             # en unidades normalizadas (1.0 = 100% del capital de referencia)

# ─── Archivos de estado y logs ────────────────────────────────────────────────
STATE_FILE = SCRIPT_DIR / "paper_trading_state.json"
TRADES_CSV = SCRIPT_DIR / "paper_trades_log.csv"
DATA_CACHE = SCRIPT_DIR / "ohlcv_1m_cache.parquet"
LOG_FILE = SCRIPT_DIR / "paper_trading.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("squeeze_paper_trader")


# ══════════════════════════════════════════════════════════════════════════
# DATOS
# ══════════════════════════════════════════════════════════════════════════

def get_exchange():
    exchange_class = getattr(ccxt, EXCHANGE_ID)
    return exchange_class({"enableRateLimit": True})


def fetch_ohlcv_paginated(exchange, symbol, timeframe, since_ms, limit_per_call=1000):
    """Descarga velas paginando hacia adelante desde since_ms hasta el presente.

    FIX: no asumir que el exchange devuelve `limit_per_call` velas por llamada.
    KuCoin Futures (y otros) a veces limita a menos (p.ej. 200) sin avisar --
    cortar la paginacion en cuanto un batch viene "corto" hacia el pasado
    detenia la descarga a los pocos cientos de velas en vez de llegar hasta
    hoy. Ahora solo se para cuando el cursor alcanza el presente o cuando el
    exchange deja de devolver velas nuevas (sin progreso)."""
    all_rows = []
    cursor = since_ms
    max_iters = 20000  # limite de seguridad para evitar un bucle infinito
    for _ in range(max_iters):
        now_ms = exchange.milliseconds()
        if cursor >= now_ms:
            break
        try:
            batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=limit_per_call)
        except Exception as e:
            log.warning(f"Error descargando OHLCV (reintentando en 5s): {e}")
            time.sleep(5)
            continue
        if not batch:
            break
        all_rows.extend(batch)
        last_ts = batch[-1][0]
        if last_ts <= cursor:
            # el exchange no avanza aunque haya devuelto datos -- evitar bucle infinito
            break
        cursor = last_ts + 1
        if len(all_rows) % 5000 < len(batch):
            log.info(f"  ...descargadas {len(all_rows):,} velas hasta "
                     f"{pd.to_datetime(last_ts, unit='ms', utc=True)}")
        time.sleep(exchange.rateLimit / 1000)
    else:
        log.warning(f"Se alcanzo el limite de seguridad de {max_iters} llamadas paginadas.")
    return all_rows


def update_data_cache(exchange):
    """Actualiza la cache local de velas de 1m. En la primera ejecucion descarga
    ~200 dias de historico (necesario para calentar el percentil de 180 dias);
    en ejecuciones posteriores solo descarga lo nuevo desde la ultima vela cacheada."""
    if DATA_CACHE.exists():
        df_cache = pd.read_parquet(DATA_CACHE)
        since_ms = int(df_cache.index[-1].timestamp() * 1000) + 1
        log.info(f"Cache existente hasta {df_cache.index[-1]}, descargando desde ahi...")
    else:
        df_cache = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        days_backfill = VOL_PCTL_LOOKBACK_DAYS + VOL_WINDOW_DAYS + 30
        since_ms = int((pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days_backfill)).timestamp() * 1000)
        log.info(f"Sin cache previa -- backfill inicial de {days_backfill} dias (puede tardar unos minutos)...")

    rows = fetch_ohlcv_paginated(exchange, SYMBOL, "1m", since_ms)
    if rows:
        df_new = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df_new["ts"] = pd.to_datetime(df_new["ts"], unit="ms", utc=True)
        df_new = df_new.set_index("ts")
        # evitar el FutureWarning de pandas al concatenar con un lado vacio
        # (pasa en la primera ejecucion, cuando df_cache aun no tiene filas)
        if len(df_cache) == 0:
            df_all = df_new
        else:
            df_all = pd.concat([df_cache, df_new])
        df_all = df_all[~df_all.index.duplicated(keep="last")].sort_index()
    else:
        df_all = df_cache

    df_all.to_parquet(DATA_CACHE)
    log.info(f"Cache actualizada: {len(df_all):,} velas de 1m, hasta {df_all.index[-1]}")
    return df_all


# ══════════════════════════════════════════════════════════════════════════
# SEÑAL (identica logica que el backtest validado)
# ══════════════════════════════════════════════════════════════════════════

def compute_daily_regime(df1m: pd.DataFrame) -> pd.DataFrame:
    daily = df1m.resample("1D").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
    )
    donch_upper = daily["high"].rolling(DONCHIAN_DAYS).max().shift(1)
    donch_lower = daily["low"].rolling(DONCHIAN_DAYS).min().shift(1)

    ret = np.log(daily["close"] / daily["close"].shift(1))
    vol = ret.rolling(VOL_WINDOW_DAYS).std() * np.sqrt(365)
    vol_pctl = vol.rolling(VOL_PCTL_LOOKBACK_DAYS).apply(
        lambda x: (x[-1] <= x).mean(), raw=True
    ).shift(1)

    was_squeeze = (
        (vol_pctl < SQUEEZE_PCTL_TH)
        .rolling(SQUEEZE_LOOKBACK_DAYS, min_periods=1)
        .max()
        .astype(bool)
    )
    return pd.DataFrame({"donch_upper": donch_upper, "donch_lower": donch_lower, "was_squeeze": was_squeeze})


def compute_15m_context(df1m: pd.DataFrame, daily_regime: pd.DataFrame) -> pd.DataFrame:
    df15 = df1m.resample("15min").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
    ).dropna()

    # FIX critico (auditoria compañero, sep-2026): descartar la ULTIMA vela de
    # 15m si esta INCOMPLETA. df1m llega hasta "ahora mismo", que casi nunca
    # coincide con un limite exacto de 15 minutos -- sin este filtro, la señal
    # se evaluaba sobre una vela a medio formar (high/low parciales), rompiendo
    # la equivalencia con el backtest, que SOLO ve velas ya cerradas. Una vela
    # de 15m que empieza en bar_start esta completa solo si ya tenemos datos de
    # 1m hasta, como minimo, el minuto bar_start+14.
    if len(df15) > 0:
        last_bar_start = df15.index[-1]
        last_bar_final_minute = last_bar_start + pd.Timedelta(minutes=14)
        if df1m.index[-1] < last_bar_final_minute:
            df15 = df15.iloc[:-1]

    tr = pd.concat([
        df15["high"] - df15["low"],
        (df15["high"] - df15["close"].shift(1)).abs(),
        (df15["low"] - df15["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df15["atr"] = tr.rolling(ATR_HOURS * 4).mean().shift(1)
    df15["vol_avg"] = df15["volume"].rolling(VOL_CONFIRM_WINDOW).mean().shift(1)
    df15["day"] = df15.index.normalize()
    mapped = daily_regime.reindex(df15["day"]).values
    df15["donch_upper"] = mapped[:, 0]
    df15["donch_lower"] = mapped[:, 1]
    df15["was_squeeze"] = mapped[:, 2]
    return df15


def check_for_new_entry(df1m: pd.DataFrame, entries_today: int, last_entry_day: str, last_processed_bar: str):
    """Comprueba si la vela de 15m MAS RECIENTE (ya cerrada) genera una señal
    de entrada valida. Devuelve un dict con la señal, o None.

    FIX (bug real detectado en paper trading, sep-2026): el cron corre cada
    minuto, pero una vela de 15m sigue siendo "la ultima" durante los 15
    minutos completos. Si una posicion se abre Y se cierra dentro de esa
    misma ventana (habitual, dado que los trades suelen resolverse en pocos
    minutos), el siguiente ciclo del cron volvia a ver la MISMA señal y
    re-entraba sobre el mismo evento -- generando entradas "fantasma"
    duplicadas (mismo entry_time, drenando el limite de MAX_ENTRIES_PER_DAY
    con eventos que no eran reentradas reales). Ahora se exige que la vela
    usada sea ESTRICTAMENTE POSTERIOR a la ultima vela ya procesada."""
    daily_regime = compute_daily_regime(df1m)
    df15 = compute_15m_context(df1m, daily_regime)

    if len(df15) < ATR_HOURS * 4 + 1:
        log.info("Aun no hay suficiente historico para operar (calentando indicadores).")
        return None

    last = df15.iloc[-1]
    bar_ts = str(df15.index[-1])
    today_str = last["day"].strftime("%Y-%m-%d")
    entries_today_actual = entries_today if last_entry_day == today_str else 0

    if last_processed_bar is not None and bar_ts <= last_processed_bar:
        return None  # esta vela ya se proceso -- evita el bug de duplicados
    if pd.isna(last["donch_upper"]) or pd.isna(last["atr"]) or last["atr"] <= 0:
        return None
    if entries_today_actual >= MAX_ENTRIES_PER_DAY:
        return None
    if not last["was_squeeze"]:
        return None
    if pd.isna(last["vol_avg"]) or last["vol_avg"] <= 0:
        return None

    break_up = last["high"] > last["donch_upper"]
    break_down = last["low"] < last["donch_lower"]
    if break_up and break_down:
        return None  # vela ambigua, se descarta
    if not (break_up or break_down):
        return None

    direction = 1 if break_up else -1
    level = last["donch_upper"] if break_up else last["donch_lower"]
    stop_dist = max(MIN_STOP_PCT * last["close"], ATR_MULT * last["atr"])

    # confirmacion de volumen prorrateada, minuto a minuto, dentro de esta vela de 15m
    bar_start = df15.index[-1]
    bar_end = bar_start + pd.Timedelta(minutes=15)
    minute_bars = df1m.loc[bar_start:bar_end - pd.Timedelta(minutes=1)]
    if len(minute_bars) == 0:
        return None

    cross_mask = (minute_bars["high"] >= level) if direction == 1 else (minute_bars["low"] <= level)
    vol_vals = minute_bars["volume"].values
    minutos = np.arange(1, len(minute_bars) + 1)
    # fix RONDA 6 (sincronizado con la version final del backtest): el volumen
    # usado para confirmar SOLO cuenta minutos ESTRICTAMENTE ANTERIORES al
    # minuto donde el precio cruza el nivel. Usar el volumen del propio minuto
    # del cruce no es causal -- en el instante exacto del cruce (p.ej. segundo
    # 3 de ese minuto) ese volumen aun no se conocia.
    cum_vol_prior = np.concatenate([[0.0], np.cumsum(vol_vals)[:-1]])
    minutos_prior = minutos - 1
    umbral = VOL_CONFIRM_MULT * last["vol_avg"] * (minutos_prior / 15)
    valid_mask = cross_mask.values & (cum_vol_prior >= umbral) & (minutos_prior >= 1)

    if not valid_mask.any():
        return None

    trig_idx = valid_mask.argmax()
    entry_time = minute_bars.index[trig_idx]
    entry_bar = minute_bars.iloc[trig_idx]
    stop_price = level - stop_dist if direction == 1 else level + stop_dist

    # comprobacion: ¿el stop tambien se toca en la propia vela de entrada?
    stopped_immediately = (
        (direction == 1 and entry_bar["low"] <= stop_price) or
        (direction == -1 and entry_bar["high"] >= stop_price)
    )

    return dict(
        entry_time=entry_time, direction=direction, donch_level=float(level),
        stop_price=float(stop_price), stop_dist=float(stop_dist),
        stop_pct=float(stop_dist / level), stopped_immediately=bool(stopped_immediately),
        day=today_str, bar_ts=bar_ts,
    )


def check_open_position_stop(df1m: pd.DataFrame, position: dict):
    """Comprueba, en las velas de 1m nuevas desde la ultima comprobacion, si el
    trailing stop salta. Convencion conservadora: comprobar antes de actualizar."""
    entry_time = pd.Timestamp(position["entry_time"])
    direction = position["direction"]
    stop_price = position["current_stop"]
    stop_dist = position["stop_dist"]

    future = df1m.loc[entry_time:]
    if len(future) < 2:
        return None, stop_price

    fh = future["high"].values
    fl = future["low"].values
    ft = future.index
    sp = stop_price

    for j in range(1, len(future)):
        if direction == 1:
            if fl[j] <= sp:
                return dict(exit_time=ft[j], exit_price=float(sp)), sp
            ns = fh[j] - stop_dist
            if ns > sp:
                sp = ns
        else:
            if fh[j] >= sp:
                return dict(exit_time=ft[j], exit_price=float(sp)), sp
            ns = fl[j] + stop_dist
            if ns < sp:
                sp = ns

    return None, sp


# ══════════════════════════════════════════════════════════════════════════
# ESTADO Y GESTION DE RIESGO
# ══════════════════════════════════════════════════════════════════════════

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            state = json.load(f)
        state.setdefault("last_processed_bar", None)  # compatibilidad con estados antiguos
        return state
    return dict(position=None, entries_today=0, last_entry_day=None, last_processed_bar=None,
                equity=INITIAL_EQUITY, peak_equity=INITIAL_EQUITY)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def position_size_multiplier(stop_pct, equity, peak_equity):
    dd = equity / peak_equity - 1
    if dd <= -HALT_DD_THRESHOLD:
        return 0.0, True
    risk_target = RISK_PCT_CHALLENGE if CURRENT_PHASE == "challenge" else RISK_PCT_FUNDED
    if dd <= -DERISK_DD_THRESHOLD:
        risk_target *= 0.5
    return min(risk_target / stop_pct, MAX_LEVERAGE), False


def log_trade_to_csv(row: dict):
    df_row = pd.DataFrame([row])
    if TRADES_CSV.exists():
        df_row.to_csv(TRADES_CSV, mode="a", header=False, index=False)
    else:
        df_row.to_csv(TRADES_CSV, mode="w", header=True, index=False)


def fetch_real_price(exchange):
    """Precio real de mercado AHORA MISMO (para comparar contra el fill teorico
    -- el nivel de Donchian o el stop -- y medir el slippage real). Usa el
    ultimo precio negociado; si el ticker no lo trae, cae al punto medio del
    bid/ask. Devuelve None si falla (no debe romper el ciclo por esto)."""
    try:
        ticker = exchange.fetch_ticker(SYMBOL)
        if ticker.get("last") is not None:
            return float(ticker["last"])
        if ticker.get("bid") and ticker.get("ask"):
            return float((ticker["bid"] + ticker["ask"]) / 2)
    except Exception as e:
        log.warning(f"No se pudo obtener el precio real de mercado: {e}")
    return None


def slippage_pct(theoretical_price: float, real_price, direction: int):
    """Slippage en %, ya ajustado por direccion. POSITIVO = el precio real es
    PEOR que el teorico (nos cuesta mas de lo que asumia el backtest).
    NEGATIVO = el precio real es MEJOR que el teorico."""
    if real_price is None or theoretical_price is None:
        return None
    return ((real_price - theoretical_price) / theoretical_price) * direction


# ══════════════════════════════════════════════════════════════════════════
# CICLO PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════

def run_once():
    exchange = get_exchange()
    df1m = update_data_cache(exchange)
    state = load_state()

    if state["position"] is not None:
        exit_info, new_stop = check_open_position_stop(df1m, state["position"])
        state["position"]["current_stop"] = new_stop

        if exit_info is not None:
            pos = state["position"]
            direction = pos["direction"]
            entry_price = pos["donch_level"]
            exit_price = exit_info["exit_price"]
            ret = (exit_price / entry_price - 1) * direction - 2 * COST_BPS / 10000

            pos_mult, _ = position_size_multiplier(pos["stop_pct"], state["equity"], state["peak_equity"])
            pnl_pct = pos_mult * ret
            state["equity"] *= (1 + pnl_pct)
            state["peak_equity"] = max(state["peak_equity"], state["equity"])

            # captura del precio REAL de mercado en el instante de la salida, para
            # comparar contra el fill teorico (nivel del stop) y medir el slippage
            real_exit_price = fetch_real_price(exchange)
            slip_exit = slippage_pct(exit_price, real_exit_price, -direction)  # al salir, la direccion del riesgo se invierte

            if slip_exit is not None:
                log.info(
                    f"CIERRE {'LARGO' if direction==1 else 'CORTO'}: entrada={entry_price:.2f} "
                    f"salida_teorica={exit_price:.2f} salida_real={real_exit_price:.2f} "
                    f"(slippage={slip_exit*100:+.3f}%) ret={ret*100:.2f}% equity={state['equity']:.4f}"
                )
            else:
                log.info(
                    f"CIERRE {'LARGO' if direction==1 else 'CORTO'}: entrada={entry_price:.2f} "
                    f"salida={exit_price:.2f} ret={ret*100:.2f}% (no se pudo obtener precio real) "
                    f"equity={state['equity']:.4f}"
                )
            log_trade_to_csv(dict(
                entry_time=pos["entry_time"], exit_time=str(exit_info["exit_time"]),
                direction=direction, entry_price=entry_price, exit_price=exit_price,
                ret_pct=ret * 100, position_mult=pos_mult, pnl_account_pct=pnl_pct * 100,
                equity_after=state["equity"], stop_pct=pos["stop_pct"],
                real_price_at_exit=real_exit_price, slippage_exit_pct=slip_exit,
            ))
            state["position"] = None
        else:
            log.info(
                f"Posicion abierta ({'LARGO' if state['position']['direction']==1 else 'CORTO'}), "
                f"stop actual={new_stop:.2f}, sigue viva."
            )
        save_state(state)
        return

    # FIX critico (halt real): comprobar el circuit breaker ANTES de buscar
    # señal nueva -- antes se calculaba "halted" pero nunca se usaba para
    # impedir la entrada, solo ponia el tamaño de posicion a 0 despues de
    # haber "abierto" la operacion en el registro.
    dd_actual = state["equity"] / state["peak_equity"] - 1
    if dd_actual <= -HALT_DD_THRESHOLD:
        log.warning(
            f"CIRCUIT BREAKER ACTIVO: drawdown actual={dd_actual*100:.1f}% <= "
            f"-{HALT_DD_THRESHOLD*100:.0f}%. No se buscan nuevas entradas hasta revision manual."
        )
        return

    signal = check_for_new_entry(df1m, state["entries_today"], state["last_entry_day"], state["last_processed_bar"])
    if signal is None:
        log.info("Sin señal nueva. Sistema en espera.")
        return

    # fix duplicados: registrar la vela usada ANTES de nada mas, para que aunque
    # el resto del proceso falle, no se vuelva a intentar sobre la misma vela
    state["last_processed_bar"] = signal["bar_ts"]

    if signal["day"] != state["last_entry_day"]:
        state["entries_today"] = 0
    state["entries_today"] += 1
    state["last_entry_day"] = signal["day"]

    if signal["stopped_immediately"]:
        direction = signal["direction"]
        ret = -signal["stop_dist"] / signal["donch_level"] - 2 * COST_BPS / 10000
        pos_mult, halted = position_size_multiplier(signal["stop_pct"], state["equity"], state["peak_equity"])
        pnl_pct = pos_mult * ret
        state["equity"] *= (1 + pnl_pct)
        state["peak_equity"] = max(state["peak_equity"], state["equity"])

        real_price = fetch_real_price(exchange)
        slip_entry = slippage_pct(signal["donch_level"], real_price, direction)

        log.warning(
            f"ENTRADA Y STOP EN LA MISMA VELA (raro pero valido): "
            f"{'LARGO' if direction==1 else 'CORTO'} teorico={signal['donch_level']:.2f} "
            f"real={real_price}, ret={ret*100:.2f}%"
        )
        log_trade_to_csv(dict(
            entry_time=str(signal["entry_time"]), exit_time=str(signal["entry_time"]),
            direction=direction, entry_price=signal["donch_level"], exit_price=signal["stop_price"],
            ret_pct=ret * 100, position_mult=pos_mult, pnl_account_pct=pnl_pct * 100,
            equity_after=state["equity"], stop_pct=signal["stop_pct"],
            real_price_at_entry=real_price, slippage_entry_pct=slip_entry,
            real_price_at_exit=real_price, slippage_exit_pct=slip_entry,
        ))
        save_state(state)
        return

    real_price_entry = fetch_real_price(exchange)
    slip_entry = slippage_pct(signal["donch_level"], real_price_entry, signal["direction"])

    state["position"] = dict(
        entry_time=str(signal["entry_time"]), direction=signal["direction"],
        donch_level=signal["donch_level"], current_stop=signal["stop_price"],
        stop_dist=signal["stop_dist"], stop_pct=signal["stop_pct"],
        real_price_at_entry=real_price_entry, slippage_entry_pct=slip_entry,
    )
    if slip_entry is not None:
        log.info(
            f"NUEVA ENTRADA {'LARGO' if signal['direction']==1 else 'CORTO'}: "
            f"nivel Donchian (teorico)={signal['donch_level']:.2f}  precio real={real_price_entry:.2f}  "
            f"slippage={slip_entry*100:+.3f}%  stop inicial={signal['stop_price']:.2f}  "
            f"entrada #{state['entries_today']} del dia."
        )
    else:
        log.info(
            f"NUEVA ENTRADA {'LARGO' if signal['direction']==1 else 'CORTO'}: "
            f"nivel Donchian={signal['donch_level']:.2f} (no se pudo obtener precio real), "
            f"stop inicial={signal['stop_price']:.2f}, entrada #{state['entries_today']} del dia."
        )
    save_state(state)


def main():
    parser = argparse.ArgumentParser(description="Paper trading del sistema BTC Volatility Squeeze Breakout")
    parser.add_argument("--once", action="store_true", help="Ejecuta una sola comprobacion y sale (para cron)")
    parser.add_argument("--loop", action="store_true", help="Bucle propio, comprueba cada 60s")
    args = parser.parse_args()

    if not args.once and not args.loop:
        print("Especifica --once (para cron) o --loop (bucle continuo). Ver --help.")
        return

    if args.once:
        run_once()
    else:
        log.info("Iniciando bucle continuo (Ctrl+C para parar)...")
        while True:
            try:
                run_once()
            except Exception as e:
                log.error(f"Error en el ciclo: {e}", exc_info=True)
            time.sleep(60)


if __name__ == "__main__":
    main()

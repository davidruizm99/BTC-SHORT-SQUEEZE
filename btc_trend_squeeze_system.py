"""
================================================================================
BTC VOLATILITY SQUEEZE BREAKOUT — SISTEMA DE TENDENCIA (version 4)
================================================================================
Sistema de ruptura direccional en BTC, filtrado por compresion previa de
volatilidad, confirmado por volumen, con reentrada intradia. Señal decidida
en velas de 15 minutos; ejecucion resuelta a resolucion de 1 minuto con
convenciones conservadoras explicitas donde no se puede saber el orden
real de los eventos dentro de una vela.

Para el compañero que valide esto: la seccion "HISTORIAL DE AUDITORIA" al
final documenta CUATRO rondas de revision y los problemas reales
encontrados en cada una. Gracias por el ultimo repaso -- encontrasteis
cosas reales que se nos habian escapado. Seguid mirando, por favor.

LOGICA
------
1. REGIMEN DE VOLATILIDAD (diario, UTC) -- sin lookahead (shift(1) explicito
   en el percentil de volatilidad, solo datos hasta el cierre de AYER).
   "Squeeze" = percentil de vol(20d) < 30% del historico de 180d, activo si
   ocurrio en los ultimos 14 dias.

2. SEÑAL DE RUPTURA (velas de 15 min): Canal Donchian de 5 dias (shift(1)).
   Se descartan velas que rompen el canal por ARRIBA Y POR ABAJO a la vez
   (señal ambigua, direccion indeterminada).

3. CONFIRMACION DE VOLUMEN -- CONFIRMABLE EN TIEMPO REAL:
   No se usa el volumen TOTAL de la vela de 15m (que solo se conoce al
   cierre) para decidir una entrada que ocurre A MITAD de esa vela. En su
   lugar, se acumula el volumen minuto a minuto DENTRO de la vela en curso
   y se compara contra un umbral PRORRATEADO (1.5x la media de volumen de
   las 20 velas ANTERIORES -- shift(1), sin incluirse a si misma -- escalado
   a la fraccion de la vela ya transcurrida). Solo se entra si, en el
   minuto exacto en que el precio cruza el canal, el volumen acumulado
   hasta ESE minuto ya supera el umbral. Esto elimina una discrepancia
   temporal real que existia en versiones anteriores (~49% de las señales
   que se tomaban antes solo eran confirmables retrospectivamente al
   cierre de la vela, no en el momento de la entrada).

4. STOP Y TRAILING -- CONVENCION CONSERVADORA EXPLICITA:
   Sin datos de tick no podemos saber si dentro de una vela ocurrio primero
   el maximo o el minimo. En vez de asumir el orden favorable (actualizar
   el trailing con el maximo de la vela y LUEGO comprobar si el minimo
   toco el stop -- lo que hacian versiones anteriores), este motor
   COMPRUEBA el stop primero (con el nivel vigente desde el minuto
   anterior) y solo si no salta, actualiza el trailing para el siguiente
   minuto. Es la convencion pesimista, no la optimista.
   Stop inicial = max(0.5% del precio, 0.5 x ATR de 24h) -- el ATR usado
   es el de la vela ANTERIOR (shift(1)), nunca el de la vela en curso.

5. REENTRADA INTRADIA (hasta 2 reintentos ademas del original, 3/dia).

6. COSTES BIDIRECCIONALES: se descuenta el coste (5bps) tanto en la
   entrada como en la salida (antes solo se restaba una vez).

GESTION DE RIESGO
-------------------
    Tamaño de posicion = (riesgo_% objetivo x equity) / distancia_del_stop.
    Riesgo objetivo: 1.0% del equity por trade (fase funded), 0.75% (fase
    challenge). Apalancamiento maximo de seguridad: 5x. De-risking
    escalonado: DD 0-5% normal, DD 5-8% mitad de riesgo, DD>8% se
    detienen nuevas entradas.

REQUISITOS DE DATOS: 1 MINUTO (no 15m). Columnas: open, high, low, close,
volume. DatetimeIndex UTC. El script avisa si detecta resolucion mas
gruesa.

REQUISITOS DE LIBRERIAS: pip install pandas numpy pyarrow

USO:
    python btc_trend_squeeze_system.py
    (busca datos de 1m en su propia carpeta; o usa --data ruta/archivo)

RESULTADOS VALIDADOS (2022-2026, Bitstamp 1 minuto, con TODOS los fixes hasta RONDA 6):
Sobre el periodo completo 2022-2026, con confirmacion de volumen causal
estricta: HOLDOUT Sharpe=3.74, MaxDD=-2.2%, ~3.01 trades/semana.

ADVERTENCIA CRITICA SOBRE EJECUCION -- LEER ANTES DE ASIGNAR CAPITAL:
   Este backtest asume fill EXACTO al nivel de ruptura (sin slippage mas
   alla del coste fijo). Un stress test con slippage fijo adicional dio:
       0.00% slippage extra -> 8.55x (numero de referencia del backtest)
       0.05%                -> 6.12x
       0.10%                -> 4.40x
       0.15%                -> 3.08x
       0.20%                -> 2.18x
       0.30%                -> 1.02x  (PUNTO DE EQUILIBRIO)
       0.50%                -> 0.22x  (perdida)
   Con un fill mas realista (open del minuto SIGUIENTE al cruce, sin
   slippage fijo adicional), el sistema da 0.59x -- PIERDE dinero. El
   edge real depende criticamente de cuanto slippage se consiga en la
   practica, y eso NO se puede determinar con mas backtesting -- hace
   falta datos de spread/order book reales de KuCoin, o mejor aun,
   trading en real/paper con capital minimo para medirlo directamente.
   NO ASIGNAR CAPITAL SIGNIFICATIVO SIN VALIDAR ESTO PRIMERO.

   Ademas: la entrada ORIGINAL de cada dia (antes de cualquier
   reentrada) tiene un edge casi nulo por si sola (WinRate~43%,
   expectancy +0.086R) -- practicamente todo el beneficio del sistema
   viene concentrado en las reentradas (2a: WR 62%, +0.76R; 3a: WR 68%,
   +1.43R). Se probo ensanchar el stop solo para la entrada original
   (multiplicadores 1.5x-3.0x) para ver si rescataba su WinRate -- NO
   funciono (el WinRate no mejora de forma consistente), asi que se
   descarto esa idea. Esto sugiere que el sistema depende
   estructuralmente del mecanismo de confirmacion por reentrada, no es
   simplemente "cualquier ruptura con squeeze y volumen".

HISTORIAL DE AUDITORIA (cronologico, por transparencia -- 4 rondas)
--------------------------------------------------------------------------------
RONDA 1 -- Lookahead en la señal de regimen (encontrado y corregido). El
   percentil de volatilidad usaba el retorno del DIA COMPLETO aplicado a
   horas de ese mismo dia que aun no habian ocurrido. Fix: shift(1).

RONDA 2 -- Ambiguedad de ejecucion intrabar a nivel de 15 minutos
   (encontrado y corregido). ~38% de las entradas tenian el nivel de
   ruptura Y el stop dentro de la MISMA vela de 15m; el backtest no sabia
   el orden real. Fix: pasar la ejecucion a resolucion de 1 minuto.
   Impacto: multiplo de capital paso de 16.45x a 7.82x -- la version de
   15m SI inflaba el resultado, aprox a la mitad.

RONDA 3 -- Revision externa (gracias al compañero), CUATRO problemas mas
   encontrados, todos reales:
     a) Lookahead de volumen: se usaba el volumen TOTAL de la vela de 15m
        (solo conocido al cierre) para validar una entrada ocurrida A
        MITAD de esa vela. ~49% de las señales tomadas solo eran
        confirmables retrospectivamente. Fix: umbral prorrateado,
        confirmable minuto a minuto en tiempo real.
     b) ATR sin shift: el ATR usado para el stop de la vela i incluia el
        rango de la propia vela i (aun sin cerrar en el momento de la
        entrada). Fix: shift(1).
     c) Volumen medio sin shift: la media de 20 velas se comparaba
        incluyendose a si misma. Fix: shift(1).
     d) Convencion favorable en el trailing (high-luego-low). Fix:
        convencion conservadora (comprobar el stop antes de actualizar
        el trailing).
     e) Costes de una sola direccion. Fix: coste en entrada Y salida.
     f) Velas que rompen el canal en ambas direcciones a la vez, tratadas
        arbitrariamente como largo. Fix: se descartan.
   Impacto combinado de TODOS estos fixes: multiplo de capital paso de
   7.82x a 8.55x (se mantuvo, incluso mejoro ligeramente) -- win rate
   bajo de 60.4% a 56.0% (señal mas exigente, menos señales, pero de
   mas calidad).

RONDA 4 -- Analisis P1 solicitados (completados) + un P0 adicional:
   a) Stress test de ejecucion (fill exacto vs siguiente open, 5/10/20bps):
      ver ADVERTENCIA CRITICA arriba -- resultado preocupante, el sistema
      pierde dinero con fills realistas de "siguiente vela".
   b) Slippage fijo escalonado (0 a 0.50%): punto de equilibrio ~0.30%.
   c) Primera entrada vs reentradas: la entrada original tiene edge casi
      nulo (+0.086R); el beneficio esta concentrado en las reentradas.
      Se intento ensanchar su stop -- no ayudo, descartado.
   d) Distribucion de R y concentracion de P&L: el 20% de trades genera
      el 69.5% del P&L positivo (perfil sano, no dependiente de un
      puñado de outliers extremos).
   e) MFE/MAE: excursion favorable media ~5x la adversa media (sano).
   f) P0 NUEVO encontrado en esta ronda: el motor no comprobaba si el
      stop tambien se tocaba DENTRO de la propia vela de entrada de 1
      minuto (solo empezaba a vigilar desde la vela siguiente). Afectaba
      al 4.6% de las entradas. CORREGIDO. Impacto: 8.55x -> 7.42x.

PENDIENTE (P1/P2, no implementado, no son bugs sino refinamientos):
   ablation study aislando cada fix por separado; validacion cross-venue
   con datos de KuCoin; estabilidad de parametros; walk-forward formal;
   Monte Carlo de robustez; optimizacion de trailing/position sizing.

RONDA 5 -- Peticiones adicionales del compañero, todas completadas:
   a) Ablation study formal (V1 -> Final, un fix a la vez): confirmo que
      el salto de Sharpe mas grande, con diferencia, ocurre al AÑADIR LA
      REENTRADA (Sharpe 1.26 -> 3.38) -- mucho mayor que la suma de
      todos los fixes de sesgo juntos (que se mueven en un rango
      estrecho, 3.35-3.61, y terminan en 3.05). Esto significa que la
      señal base (ruptura simple, sin reentrada) es debil por si sola
      (Sharpe ~1.1-1.5) y que el sistema depende estructuralmente del
      mecanismo de reentrada.
   b) Tabla completa por numero de entrada (WR/PF/Expectancy/Sharpe
      aislado): confirmado el patron "Escenario B" -- Entrada 1 tiene
      Sharpe aislado ~-0.01 (sin edge), Reentrada 1 Sharpe ~3.37,
      Reentrada 2 Sharpe ~4.07. Investigado el porque: cuando NO hay
      reentrada valida (squeeze/volumen no se reconfirman), el precio
      revierte de verdad (-1.63% a 24h) -- son rupturas falsas
      genuinas. Cuando SI hay reentrada, comportamiento neutro/
      favorable. El filtro de squeeze+volumen en la reentrada esta
      discriminando genuinamente entre "ruido" y "expansion fallida
      con compresion aun intacta", no es un artefacto.
   c) Independencia estadistica real: 660 trades proceden de solo 16
      episodios de squeeze independientes (racha continua de dias con
      squeeze activo). Leave-one-episode-out: muy estable (rango de
      Sharpe 1.13-1.30 segun que episodio se excluya, ninguno domina
      el resultado). Block bootstrap por episodio (5000 remuestreos):
      P(perdida)=0%, percentil 5=3.84x. Pero el holdout real solo tiene
      4 episodios independientes detras de sus 244 trades -- la
      potencia estadistica real es menor de lo que sugiere el conteo
      de trades.
   d) Largo/corto: simetrico (2.76x vs 3.10x), el patron entrada1-debil/
      reentrada-fuerte se repite en ambas direcciones por separado.
   e) Regimenes de BTC: positivo en BULL/BEAR/LATERAL (expectancy
      0.54-0.66R en los tres). Mejor en baja volatilidad relativa que
      en alta (esperable, el propio filtro de squeeze busca baja vol).
   f) Stress de ejecucion ampliado (5/10/20/30/50bps por lado, sobre
      COST_BPS en vez de slippage en el fill): punto de ruptura entre
      20 y 30bps/lado -- coherente con el ~30bps encontrado antes via
      slippage en el fill. Confirma: la viabilidad real depende
      criticamente de conseguir ejecucion con menos de esa cifra.
   g) MAX_ENTRIES_PER_DAY explorado como logica de regimen (episodio de
      squeeze en vez de dia calendario), tal y como propuso el
      compañero -- IMPORTANTE, PROBADO Y DESCARTADO: se probaron topes
      de 3 a 20 por episodio y tambien sin tope. Ninguna variante
      supera al sistema actual (dia calendario, tope=3) en TRAIN Y VAL
      simultaneamente -- la mayoria da Sharpe menor pese a mas trades.
      Sin tope, la frecuencia se dispara sin saturar naturalmente
      (hasta 9 trades/semana, episodios con 28 reentradas) sin que el
      Sharpe mejore de forma consistente. CONCLUSION: se mantiene
      MAX_ENTRIES_PER_DAY=3 por dia calendario tal cual estaba --
      pese a ser conceptualmente "tosco", empiricamente funciona mejor
      que las alternativas mas principled que se probaron.
   h) Distribucion real de trades/semana (no solo la media): la media
      de 3.32/semana esconde una distribucion muy irregular -- 48.7%
      de las semanas NO opera ni un trade, con rachas de hasta 22
      semanas seguidas sin actividad. Cuando opera, lo hace a rafagas
      (algunas semanas con 10-15 trades). Esto es coherente con que
      solo hay 16 episodios de squeeze independientes en 4 años: el
      sistema concentra su actividad en esos episodios y calla el
      resto del tiempo. Importante para expectativas de cara a
      requisitos de frecuencia minima de una prop firm.

RONDA 6 -- Otro problema real encontrado por el compañero (gracias de
nuevo), ESTE SI SE CORRIGIO EN EL CODIGO:
   Confirmacion de volumen NO CAUSAL dentro del propio minuto del cruce.
   La version anterior comparaba el volumen TOTAL del minuto en el que
   el precio cruzaba el nivel (incluyendo segundos posteriores al cruce
   exacto) contra el umbral -- en el instante preciso del cruce, ese
   volumen aun no se conocia. Corregido exigiendo que la condicion de
   volumen ya estuviera confirmada usando SOLO minutos ESTRICTAMENTE
   ANTERIORES al del cruce (ver check_for_new_entry en el codigo).
   Impacto: mejora en las tres ventanas a la vez (Sharpe TRAIN 3.47->
   3.59, VAL 3.45->3.82, HOLDOUT 3.27->3.74), con practicamente los
   mismos trades (se descartan solo las señales que dependian de ese
   volumen no causal, que resultaron ser ligeramente peores).

   PENDIENTE, sugerido por el compañero para llevar esto a nivel
   profesional: reconstruir con datos de trades/ticks (no solo velas de
   1 minuto) para saber el momento EXACTO del cruce, el volumen
   realmente disponible antes de ese instante, y modelar slippage y
   prioridad de la orden de forma realista. Esto NO esta implementado
   -- requeriria una fuente de datos de tick que aun no tenemos.

PROBLEMAS QUE SIGUEN SIN RESOLVERSE DEL TODO:
   - La ambiguedad high/low sigue existiendo a nivel de 1 minuto (mucho
     mas acotada que a 15m, pero no eliminada sin datos de tick).
   - Riesgo de sobreajuste por multiples consultas al mismo holdout
     durante el desarrollo -- no se corrige con codigo, solo con tiempo
     real fuera de muestra.
   - Datos de Bitstamp, no del venue real de operativa (KuCoin) --
     correlacion 0.9999 con Binance en el periodo solapado, pero sigue
     siendo un venue distinto.

Si encontrais algo mas, por favor decidnoslo. Este documento debe seguir
actualizandose cada vez que se corrija algo.
================================================================================
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent

# ─── Parametros del sistema (validados) ─────────────────────────────────────
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
MAX_ENTRIES_PER_DAY = 3          # original + hasta 2 reentradas
COST_BPS = 5                     # coste POR LADO (se aplica en entrada Y salida)
MAX_TRADE_DURATION_DAYS = 10

# ─── Gestion de riesgo / money management ───────────────────────────────────
RISK_PCT_CHALLENGE = 0.0075
RISK_PCT_FUNDED = 0.0100
MAX_LEVERAGE = 5.0
DERISK_DD_THRESHOLD = 0.05
HALT_DD_THRESHOLD = 0.08

# ─── Splits temporales (ajustar segun el rango de datos disponible) ─────────
TRAIN_END = pd.Timestamp("2022-12-31", tz="UTC")
VAL_END = pd.Timestamp("2024-12-31", tz="UTC")


def compute_daily_regime(df1m: pd.DataFrame) -> pd.DataFrame:
    """Canal Donchian(5) y regimen de squeeze, a nivel diario, sin lookahead."""
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


def compute_15m_signals(df1m: pd.DataFrame, daily_regime: pd.DataFrame) -> pd.DataFrame:
    """Agrega a 15m. ATR y volumen medio con shift(1) -- ninguno de los dos
    incluye la propia vela que esta siendo evaluada (ver RONDA 3 del
    historial de auditoria)."""
    df15 = df1m.resample("15min").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
    ).dropna()

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


def backtest_1min_execution(df1m: pd.DataFrame) -> pd.DataFrame:
    """Motor principal, con todos los fixes P0 aplicados (ver docstring)."""
    daily_regime = compute_daily_regime(df1m)
    df15 = compute_15m_signals(df1m, daily_regime)

    price15 = df15["close"].values
    high15 = df15["high"].values
    low15 = df15["low"].values
    vol_avg = df15["vol_avg"].values
    atr = df15["atr"].values
    donch_upper = df15["donch_upper"].values
    donch_lower = df15["donch_lower"].values
    squeeze = df15["was_squeeze"].values
    days15 = df15["day"].values
    idx15 = df15.index
    n15 = len(df15)

    df1m_sorted = df1m.sort_index()
    trades = []
    entries_today = 0
    cur_day = None
    start_i = ATR_HOURS * 4
    i = start_i

    while i < n15:
        d = days15[i]
        if d != cur_day:
            cur_day = d
            entries_today = 0

        do_entry = True
        if np.isnan(donch_upper[i]) or np.isnan(atr[i]) or atr[i] <= 0:
            do_entry = False
        elif entries_today >= MAX_ENTRIES_PER_DAY:
            do_entry = False
        elif not squeeze[i]:
            do_entry = False
        elif np.isnan(vol_avg[i]) or vol_avg[i] <= 0:
            do_entry = False

        if do_entry:
            break_up = high15[i] > donch_upper[i]
            break_down = low15[i] < donch_lower[i]

            if break_up and break_down:
                # vela ambigua: rompe en ambas direcciones, se descarta (fix P0)
                i += 1
                continue

            if break_up or break_down:
                direction = 1 if break_up else -1
                level = donch_upper[i] if break_up else donch_lower[i]
                stop_dist = max(MIN_STOP_PCT * price15[i], ATR_MULT * atr[i])

                bar_start = idx15[i]
                bar_end = bar_start + pd.Timedelta(minutes=15)
                minute_bars = df1m_sorted.loc[bar_start:bar_end - pd.Timedelta(minutes=1)]

                if len(minute_bars) > 0:
                    cross_mask = (minute_bars["high"] >= level) if direction == 1 else (minute_bars["low"] <= level)
                    vol_vals = minute_bars["volume"].values
                    minutos = np.arange(1, len(minute_bars) + 1)
                    # fix RONDA 6: el volumen usado para confirmar SOLO cuenta minutos
                    # ESTRICTAMENTE ANTERIORES al minuto donde el precio cruza el nivel.
                    # Antes se incluia el volumen del propio minuto del cruce completo,
                    # que en el instante exacto del cruce (p.ej. segundo 3 de ese minuto)
                    # aun no se conocia -- no era causal.
                    cum_vol_prior = np.concatenate([[0.0], np.cumsum(vol_vals)[:-1]])
                    minutos_prior = minutos - 1
                    umbral_prorrateado = VOL_CONFIRM_MULT * vol_avg[i] * (minutos_prior / 15)
                    valid_mask = cross_mask.values & (cum_vol_prior >= umbral_prorrateado) & (minutos_prior >= 1)

                    if valid_mask.any():
                        trig_idx = valid_mask.argmax()
                        entry_time = minute_bars.index[trig_idx]
                        entry_price = level
                        stop_price = entry_price - stop_dist if direction == 1 else entry_price + stop_dist

                        # fix RONDA 4: comprobar si el stop TAMBIEN se toca en la propia
                        # vela de entrada (antes solo se comprobaba a partir de la siguiente)
                        entry_bar = df1m_sorted.loc[entry_time]
                        stopped_immediately = (
                            (direction == 1 and entry_bar["low"] <= stop_price) or
                            (direction == -1 and entry_bar["high"] >= stop_price)
                        )
                        if stopped_immediately:
                            ret = -stop_dist / entry_price - 2 * COST_BPS / 10000
                            stop_pct = stop_dist / entry_price
                            trades.append((entry_time, entry_time, direction, ret, stop_pct, d))
                            entries_today += 1
                            i += 1
                            continue

                        future = df1m_sorted.loc[entry_time:]
                        max_bars = 60 * 24 * MAX_TRADE_DURATION_DAYS

                        if len(future) >= 2:
                            fh = future["high"].values
                            fl = future["low"].values
                            ft = future.index
                            exit_price = None
                            exit_time = None
                            for j in range(1, min(len(future), max_bars)):
                                if direction == 1:
                                    # convencion conservadora: comprobar ANTES de actualizar (fix P0)
                                    if fl[j] <= stop_price:
                                        exit_price = stop_price
                                        exit_time = ft[j]
                                        break
                                    new_stop = fh[j] - stop_dist
                                    if new_stop > stop_price:
                                        stop_price = new_stop
                                else:
                                    if fh[j] >= stop_price:
                                        exit_price = stop_price
                                        exit_time = ft[j]
                                        break
                                    new_stop = fl[j] + stop_dist
                                    if new_stop < stop_price:
                                        stop_price = new_stop

                            if exit_price is not None:
                                # coste bidireccional: entrada Y salida (fix P0)
                                ret = (exit_price / entry_price - 1) * direction - 2 * COST_BPS / 10000
                                stop_pct = stop_dist / entry_price
                                trades.append((entry_time, exit_time, direction, ret, stop_pct, d))
                                entries_today += 1
                                next_i = idx15.searchsorted(exit_time, side="right")
                                i = max(next_i, i + 1)
                                continue
        i += 1

    return pd.DataFrame(trades, columns=["entry_time", "exit_time", "direction", "ret", "stop_pct", "day"])


def simulate_equity_with_risk_layer(trades: pd.DataFrame, phase: str = "funded") -> pd.DataFrame:
    """Sizing por distancia de stop + de-risking escalonado por drawdown."""
    risk_target = RISK_PCT_CHALLENGE if phase == "challenge" else RISK_PCT_FUNDED
    equity = 1.0
    peak = 1.0
    halted = False
    rows = []
    for _, t in trades.iterrows():
        dd = equity / peak - 1
        if dd <= -HALT_DD_THRESHOLD:
            halted = True
        risk_pct = risk_target if dd > -DERISK_DD_THRESHOLD else risk_target * 0.5
        if halted:
            rows.append((t["exit_time"], equity, dd, 0.0, True))
            continue
        position_mult = min(risk_pct / t["stop_pct"], MAX_LEVERAGE)
        pnl_pct = position_mult * t["ret"]
        equity *= (1 + pnl_pct)
        peak = max(peak, equity)
        rows.append((t["exit_time"], equity, equity / peak - 1, pnl_pct, False))
    return pd.DataFrame(rows, columns=["exit_time", "equity", "drawdown", "trade_pnl_pct", "halted"])


def period_stats(trades: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    sub = trades[(trades.exit_time > start) & (trades.exit_time <= end)].copy()
    if len(sub) < 5:
        return dict(sharpe=np.nan, maxdd=np.nan, cagr=np.nan, trades_per_week=0.0, n=len(sub), winrate=np.nan)

    sub["day_str"] = pd.to_datetime(sub["day"]).dt.strftime("%Y-%m-%d")
    daily_ret = sub.groupby("day_str")["ret"].sum()
    all_days = pd.date_range(start, end, freq="D", tz="UTC").strftime("%Y-%m-%d")
    daily_ret = daily_ret.reindex(all_days, fill_value=0.0)

    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(365) if daily_ret.std() > 0 else np.nan
    equity = np.exp(np.cumsum(np.log1p(daily_ret.values)))
    maxdd = (equity / np.maximum.accumulate(equity) - 1).min()

    days_span = (end - start).days
    weeks = days_span / 7
    n_years = days_span / 365.25
    cagr = equity[-1] ** (1 / n_years) - 1 if n_years > 0 else np.nan

    return dict(
        sharpe=sharpe, maxdd=maxdd, cagr=cagr, trades_per_week=len(sub) / weeks,
        n=len(sub), winrate=(sub["ret"] > 0).mean(),
    )


def print_report(trades: pd.DataFrame, data_start: pd.Timestamp, data_end: pd.Timestamp):
    periods = [
        ("TRAIN", data_start, min(TRAIN_END, data_end)),
        ("VAL", TRAIN_END, min(VAL_END, data_end)),
        ("HOLDOUT", VAL_END, data_end),
        ("TODO EL PERIODO", data_start, data_end),
    ]
    print(f"\n{'Periodo':<18}{'Sharpe':>9}{'MaxDD':>9}{'CAGR':>9}{'Trades/sem':>12}{'WinRate':>10}{'N':>7}")
    print("-" * 74)
    for name, start, end in periods:
        if start >= end:
            continue
        st = period_stats(trades, start, end)
        if np.isnan(st["sharpe"]):
            print(f"{name:<18}{'--':>9}{'--':>9}{'--':>9}{'--':>12}{'--':>10}{st['n']:>7}  (pocos trades)")
            continue
        print(
            f"{name:<18}{st['sharpe']:>9.2f}{st['maxdd']*100:>8.1f}%{st['cagr']*100:>8.1f}%"
            f"{st['trades_per_week']:>12.2f}{st['winrate']*100:>9.1f}%{st['n']:>7}"
        )
    print()


def find_data_file() -> Path:
    candidates = sorted(SCRIPT_DIR.glob("*.parquet")) + sorted(SCRIPT_DIR.glob("*.csv"))
    if not candidates:
        sys.exit(
            f"No se encontro ningun .parquet/.csv en {SCRIPT_DIR}\n"
            f"Coloca el archivo de datos de 1 MINUTO ahi, o indica la ruta con --data."
        )
    if len(candidates) > 1:
        print(f"Varios archivos de datos encontrados en {SCRIPT_DIR}, usando: {candidates[0].name}")
        print(f"  (ignorados: {', '.join(c.name for c in candidates[1:])})")
    return candidates[0]


def main():
    parser = argparse.ArgumentParser(description="BTC Volatility Squeeze Breakout backtest (v4, fixes P0)")
    parser.add_argument(
        "--data", default=None,
        help="Ruta al parquet/csv con OHLCV de 1 MINUTO (UTC). Si no se indica, "
             "se busca automaticamente en la carpeta de este script.",
    )
    args = parser.parse_args()

    data_path = Path(args.data) if args.data else find_data_file()
    print(f"Cargando datos desde: {data_path}")

    if data_path.suffix == ".csv":
        df = pd.read_csv(data_path, index_col=0, parse_dates=True)
    else:
        df = pd.read_parquet(data_path)

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    required_cols = {"open", "high", "low", "close", "volume"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas en los datos: {missing}")

    df = df.sort_index()

    median_gap = df.index.to_series().diff().median()
    if median_gap > pd.Timedelta(minutes=2):
        print(f"\n{'!'*78}")
        print(f"AVISO: la resolucion de los datos cargados parece ser de ~{median_gap},")
        print("no de 1 minuto. Los fixes de esta version dependen de tener datos de 1")
        print("minuto para funcionar correctamente (confirmacion de volumen prorrateada,")
        print("trailing conservador). Con datos mas gruesos, los resultados no seran")
        print("fiables. Ver HISTORIAL DE AUDITORIA en la cabecera del script.")
        print(f"{'!'*78}\n")

    trades = backtest_1min_execution(df)

    print(f"\nDatos: {df.index[0]} -> {df.index[-1]}  ({len(df):,} barras)")
    print(f"Total de trades generados: {len(trades)}")

    print("\n=== SEÑAL PURA (100% capital nocional por trade, sin money management) ===")
    print_report(trades, df.index[0], df.index[-1])

    print(f"=== CON CAPA DE RIESGO (fase FUNDED, riesgo objetivo {RISK_PCT_FUNDED*100:.2f}% equity/trade) ===")
    eq_funded = simulate_equity_with_risk_layer(trades, phase="funded")
    print(f"  Equity final: {eq_funded['equity'].iloc[-1]:.2f}x el capital inicial")
    print(f"  Drawdown maximo: {eq_funded['drawdown'].min()*100:.1f}%")
    print(f"  Trades bloqueados por circuit breaker: {eq_funded['halted'].sum()}")

    print(f"\n=== CON CAPA DE RIESGO (fase CHALLENGE, riesgo objetivo {RISK_PCT_CHALLENGE*100:.2f}% equity/trade) ===")
    eq_challenge = simulate_equity_with_risk_layer(trades, phase="challenge")
    print(f"  Equity final: {eq_challenge['equity'].iloc[-1]:.2f}x el capital inicial")
    print(f"  Drawdown maximo: {eq_challenge['drawdown'].min()*100:.1f}%")
    print(f"  Trades bloqueados por circuit breaker: {eq_challenge['halted'].sum()}")

    print("\n" + "=" * 78)
    print("IMPORTANTE: lee 'HISTORIAL DE AUDITORIA' en el docstring de cabecera.")
    print("Van 4 rondas de revision con problemas reales encontrados y corregidos.")
    print("Sigue habiendo P1/P2 pendientes (no son bugs, son refinamientos) y al")
    print("menos un problema estructural que no se corrige con codigo (riesgo de")
    print("sobreajuste por multiples consultas al holdout). Si encontras algo mas,")
    print("por favor decidnoslo.")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()

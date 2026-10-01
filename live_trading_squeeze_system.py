"""
================================================================================
BTC VOLATILITY SQUEEZE BREAKOUT — TRADING REAL EN KUCOIN
================================================================================
Version de EJECUCION REAL del sistema (coloca ordenes de verdad, con dinero
real). Reutiliza EXACTAMENTE la misma logica de señal que
paper_trading_squeeze_system.py (funciones copiadas literalmente, no
reimplementadas, para garantizar que la decision de cuando entrar/salir es
identica) y añade una capa de ejecucion real por encima.

ADVERTENCIA MUY IMPORTANTE -- LEER ANTES DE USAR, EN SERIO
--------------------------------------------------------------------------------
A fecha de escribir esto, NO teniais todavia ningun trade de paper trading
con datos de slippage real suficientes para saber si el sistema sobrevive a
la ejecucion real. El backtest tolera hasta ~15-25bps de slippage efectivo
por lado antes de perder el edge por completo -- esa es EXACTAMENTE la
pregunta que el paper trading estaba midiendo, y para la que hacian falta
10-15 trades como minimo. Pasar a real sin esa confirmacion es aceptar un
riesgo que este mismo proyecto identifico como el mas importante sin
resolver. Esto no es una advertencia legal generica -- es la conclusion
literal de la auditoria que hicimos juntos.

Recomendacion: seguid en paper hasta tener esos datos. Si decidis pasar a
real de todas formas, hacedlo con el tamaño MINIMO posible al principio,
no con el sizing completo de 1% de riesgo desde el primer trade.

SEGURIDAD DEL SCRIPT (varias capas, a proposito redundantes)
--------------------------------------------------------------------------------
1. DRY_RUN=True por defecto (abajo, en la configuracion). Con DRY_RUN=True,
   el script hace TODO igual que en real (calcula señales, "abriria"
   posiciones) pero NUNCA llama a create_order -- solo lo registra en el
   log. Es la forma de probar este script exacto, con las claves API reales
   conectadas, sin arriesgar nada.
2. `DRY_RUN` es el UNICO interruptor: cambiarlo a False en el codigo basta
   para que las señales siguientes coloquen ordenes reales. Ya no exige
   ningun flag adicional en la linea de comandos (se quito a peticion
   explicita del usuario) -- si haceis pruebas conjuntas, aseguraos de que
   quien edite el codigo sepa que ese cambio, por si solo, ya es
   suficiente para operar en real la proxima vez que corra el cron.
3. Reconciliacion al arrancar: antes de hacer nada, compara la posicion que
   el exchange dice que teneis abierta contra lo que el estado local
   (json) cree que hay. Si no coinciden, el script se PARA y no hace nada
   mas hasta que lo resolvais a mano -- evita duplicar o perder el rastro
   de una posicion.
4. Las claves API se leen de variables de entorno, NUNCA se escriben en
   este archivo. Ver "CREDENCIALES" mas abajo.
5. Circuit breaker de drawdown (-8%) igual que en paper -- bloquea nuevas
   entradas, no coloca ninguna orden nueva si salta.

CREDENCIALES -- COMO CONFIGURARLAS (nunca las escribais en este archivo)
--------------------------------------------------------------------------------
Antes de ejecutar, exportad estas variables de entorno en el VPS:

    export KUCOIN_API_KEY="vuestra_api_key"
    export KUCOIN_API_SECRET="vuestro_secret"
    export KUCOIN_API_PASSWORD="vuestra_passphrase_de_la_api"

Para que persistan entre sesiones sin tener que exportarlas cada vez, lo
mas limpio es añadirlas a un archivo que SOLO vosotros podais leer, p.ej.
/root/.kucoin_env, con permisos 600:

    chmod 600 /root/.kucoin_env

Y cargarlo antes de ejecutar el script (o desde la propia linea de cron):

    source /root/.kucoin_env && python3 live_trading_squeeze_system.py --once

Generad las claves en KuCoin con permisos LIMITADOS -- solo trading de
futuros, SIN permiso de retirada (withdraw). Asi, si las claves se
comprometieran, el daño maximo posible esta acotado.

NOTIFICACIONES (opcional -- si no se configura ninguna, el script sigue
funcionando igual, simplemente sin avisos. Se puede configurar email,
Telegram, o los dos a la vez -- avisa por todo lo que este configurado)
--------------------------------------------------------------------------------
Avisa en 4 momentos: entrada abierta, cierre de posicion, circuit breaker
activado, y fallo de reconciliacion (el mas importante -- significa que el
estado local y KuCoin no coinciden). NO avisa en cada ciclo sin señal,
seria demasiado ruido.

--- Email ---
Añadid estas variables al mismo archivo de antes (/root/.kucoin_env):

    export EMAIL_SMTP_USER="tu_correo@gmail.com"
    export EMAIL_SMTP_PASSWORD="tu_contraseña_de_aplicacion"
    export EMAIL_TO="donde_quieres_recibir_los_avisos@ejemplo.com"

Con Gmail, "contraseña de aplicacion" NO es vuestra contraseña normal --
hay que generarla aparte: activar verificacion en dos pasos en la cuenta
de Google, y despues en myaccount.google.com -> Seguridad -> Contraseñas
de aplicaciones, crear una para "Correo". Esa cadena de 16 caracteres es
la que va en EMAIL_SMTP_PASSWORD.

Si usais otro proveedor de correo (no Gmail), añadid tambien:

    export EMAIL_SMTP_HOST="smtp.tu-proveedor.com"
    export EMAIL_SMTP_PORT="587"

--- Telegram ---
Mas simple que el email, solo hacen falta dos valores. Si ya teneis un bot
de Telegram de otro proyecto (p.ej. el monitor de ZW), podeis reutilizar
el mismo bot -- solo teneis que usar el chat_id de esta conversacion.

1. Si no teneis bot todavia: hablad con @BotFather en Telegram, /newbot,
   seguid las instrucciones -- os da un token con forma
   "123456789:ABCdefGHIjklMNOpqrsTUVwxyz".
2. Averiguar vuestro chat_id: escribid cualquier mensaje al bot que acabais
   de crear, y despues visitad en el navegador (sustituyendo el token):
       https://api.telegram.org/bot<VUESTRO_TOKEN>/getUpdates
   En la respuesta JSON buscad "chat":{"id": ...} -- ese numero es el
   chat_id.
3. Añadid al mismo archivo /root/.kucoin_env:

    export TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
    export TELEGRAM_CHAT_ID="vuestro_chat_id"

QUE HACE DE FORMA DISTINTA A LA VERSION DE PAPER
--------------------------------------------------------------------------------
- Coloca ordenes de MERCADO reales (no simuladas) tanto para entrar como
  para cerrar por stop.
- Calcula el tamaño de la orden a partir del balance REAL de la cuenta
  (no de un equity normalizado ficticio como en paper).
- Reconcilia el estado local contra la posicion real del exchange en cada
  ejecucion, no solo al arrancar.
- Registra el fill REAL devuelto por el exchange (precio medio de
  ejecucion de la orden), que sustituye a la estimacion de "precio real de
  mercado" que se usaba en paper -- aqui ya no hace falta estimar el
  slippage, se mide directamente comparando el nivel Donchian teorico
  contra el precio de fill real de la orden.

RIESGO SIN RESOLVER (revision externa, oct-2026) -- TRES INSTRUMENTOS, UN SOLO
JUEGO DE PARAMETROS:
   El backtest se valido sobre Bitstamp/Binance SPOT. Este script opera un
   PERPETUO (COIN-M). Cada sistema calcula su propio canal Donchian con los
   datos de su propio exchange -- no hay niveles "importados" del backtest --
   pero el sistema NUNCA se ha validado historicamente sobre datos de
   perpetuo especificamente. Los perpetuos tienen dinamicas que el spot no
   tiene (funding periodico, mechas de liquidaciones en cascada) que podrian
   hacer que el canal Donchian se comporte distinto justo en el instrumento
   donde opera el capital real. No resuelto -- pendiente de conseguir
   historico de KuCoin Futures para validar directamente sobre perpetuo.

CONTRATO: COIN-M (perpetuo INVERSO XBTUSDM, simbolo ccxt BTC/USD:BTC)
--------------------------------------------------------------------------------
- El colateral y el PnL estan en BTC. Necesitais BTC (no USDT) transferido a
  la cuenta de Futures de KuCoin.
- Cada contrato vale una cantidad fija de USD (se espera 1 USD; el script se
  niega a operar si KuCoin/ccxt devuelven otro valor). Contratos = nocional
  USD / contractSize, con nocional USD = multiplicador * equity_BTC * precio.
- El equity, el pico y el circuit breaker se miden EN BTC. Esto mide el
  rendimiento del sistema sin el efecto del precio de BTC sobre el colateral,
  pero OJO: el valor en USD de la cuenta lleva una exposicion larga
  permanente a BTC que el backtest NO tiene (con la cuenta plana, si BTC cae
  un 10% el valor en USD cae un 10% sin que el sistema haya hecho nada). Con
  una posicion larga la exposicion neta a BTC es mayor que la del backtest;
  con una corta, menor.
- El retorno por trade en BTC (contrato inverso) es casi igual al retorno en
  precio del backtest para movimientos de ~0.5% (un stop de -0.5% en precio
  son -0.5025% en BTC), pero no identico. El CSV guarda ambos.
- Salvo lo anterior, la logica de señal, stops y costes es la misma.
- NO VERIFICADO contra KuCoin real desde el entorno donde se escribio: la
  forma en que ccxt devuelve el balance COIN-M, el modo de margen y el
  apalancamiento. Ejecutad --test-connection y comprobad cada dato.

REQUISITOS: pip install ccxt pandas numpy pyarrow --break-system-packages

USO:
    python live_trading_squeeze_system.py --once           # respeta el DRY_RUN del codigo
================================================================================
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import ccxt
except ImportError:
    ccxt = None  # se comprueba solo cuando hace falta (get_exchange) -- asi
                 # --test-notify puede funcionar sin ccxt instalado

SCRIPT_DIR = Path(__file__).resolve().parent

# ─── INTERRUPTOR PRINCIPAL ────────────────────────────────────────────────────
# Con DRY_RUN=True, el script hace todo el proceso pero JAMAS coloca una orden
# real -- solo registra en el log lo que habria hecho. Con DRY_RUN=False, la
# PROXIMA señal que se procese coloca una orden real -- no hace falta ningun
# flag adicional en la linea de comandos.
DRY_RUN = True

# ─── Configuracion del exchange: COIN-M (perpetuo INVERSO, colateral en BTC) ─
EXCHANGE_ID = "kucoinfutures"
SYMBOL = "BTC/USD:BTC"          # XBTUSDM en KuCoin: inverso, liquida en BTC
COLLATERAL = "BTC"              # moneda del margen y del PnL
# En un contrato inverso de KuCoin cada contrato vale una cantidad FIJA de USD
# (contractSize). Se espera 1 USD por contrato. Si ccxt/KuCoin devuelven otro
# valor, el script NO opera (fail-safe) hasta que lo verifiqueis y cambieis
# esta constante a conciencia.
EXPECTED_INVERSE_CONTRACT_USD = 1.0

# ─── Parametros del sistema (IDENTICOS al backtest y al paper -- no tocar
#     sin actualizar los tres sitios a la vez) ───────────────────────────────
DONCHIAN_DAYS = 5
VOL_WINDOW_DAYS = 20
VOL_PCTL_LOOKBACK_DAYS = 180
SQUEEZE_PCTL_TH = 0.30
SQUEEZE_LOOKBACK_DAYS = 14
ATR_HOURS = 24
ATR_MULT = 0.5
MIN_STOP_PCT = 0.005
VOL_CONFIRM_MULT = 1.5
VOL_CONFIRM_WINDOW = 20
MAX_ENTRIES_PER_DAY = 3
COST_BPS = 5

# ─── Gestion de riesgo ────────────────────────────────────────────────────────
RISK_PCT_FUNDED = 0.0100
RISK_PCT_CHALLENGE = 0.0075
CURRENT_PHASE = "funded"
MAX_LEVERAGE = 5.0
DERISK_DD_THRESHOLD = 0.05
HALT_DD_THRESHOLD = 0.08

# ─── Archivos de estado y logs (separados de los de paper trading a proposito,
#     para que nunca se mezclen los dos historiales) ─────────────────────────
STATE_FILE = SCRIPT_DIR / "live_trading_state.json"
TRADES_CSV = SCRIPT_DIR / "live_trades_log.csv"
DATA_CACHE = SCRIPT_DIR / "ohlcv_1m_cache_live.parquet"   # archivo PROPIO -- nunca
                                                             # compartir este archivo con
                                                             # paper_trading_squeeze_system.py
                                                             # si ambos corren a la vez por
                                                             # cron (riesgo de que se pisen
                                                             # al escribir en el mismo instante)
LOG_FILE = SCRIPT_DIR / "live_trading.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("squeeze_live_trader")


# ══════════════════════════════════════════════════════════════════════════
# EXCHANGE Y CREDENCIALES
# ══════════════════════════════════════════════════════════════════════════

def get_exchange():
    """Crea el objeto exchange. Si faltan credenciales, funciona igualmente
    para datos publicos (lectura de velas), pero fallara en cuanto se intente
    leer balance/posicion o colocar una orden -- por diseño, para que un
    despiste de configuracion no permita operar sin credenciales validas."""
    if ccxt is None:
        sys.exit("Falta ccxt. Instalar con: pip install ccxt --break-system-packages")

    api_key = os.environ.get("KUCOIN_API_KEY")
    api_secret = os.environ.get("KUCOIN_API_SECRET")
    api_password = os.environ.get("KUCOIN_API_PASSWORD")

    if not DRY_RUN and not (api_key and api_secret and api_password):
        sys.exit(
            "DRY_RUN=False pero faltan credenciales (KUCOIN_API_KEY / "
            "KUCOIN_API_SECRET / KUCOIN_API_PASSWORD como variables de "
            "entorno). No se puede operar en real sin ellas. Abortando."
        )

    exchange_class = getattr(ccxt, EXCHANGE_ID)
    config = {"enableRateLimit": True}
    if api_key:
        config.update({"apiKey": api_key, "secret": api_secret, "password": api_password})
    return exchange_class(config)


# ══════════════════════════════════════════════════════════════════════════
# DATOS (identico a paper trading)
# ══════════════════════════════════════════════════════════════════════════

def fetch_ohlcv_paginated(exchange, symbol, timeframe, since_ms, limit_per_call=1000):
    all_rows = []
    cursor = since_ms
    max_iters = 20000
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
    if DATA_CACHE.exists():
        df_cache = pd.read_parquet(DATA_CACHE)
        since_ms = int(df_cache.index[-1].timestamp() * 1000) + 1
        log.info(f"Cache existente hasta {df_cache.index[-1]}, descargando desde ahi...")
    else:
        df_cache = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        days_backfill = VOL_PCTL_LOOKBACK_DAYS + VOL_WINDOW_DAYS + 30
        since_ms = int((pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days_backfill)).timestamp() * 1000)
        log.info(f"Sin cache previa -- backfill inicial de {days_backfill} dias...")

    rows = fetch_ohlcv_paginated(exchange, SYMBOL, "1m", since_ms)
    if rows:
        df_new = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df_new["ts"] = pd.to_datetime(df_new["ts"], unit="ms", utc=True)
        df_new = df_new.set_index("ts")
        df_all = df_new if len(df_cache) == 0 else pd.concat([df_cache, df_new])
        df_all = df_all[~df_all.index.duplicated(keep="last")].sort_index()
    else:
        df_all = df_cache

    df_all = detect_and_refill_gaps(exchange, df_all)

    df_all.to_parquet(DATA_CACHE)
    log.info(f"Cache actualizada: {len(df_all):,} velas de 1m, hasta {df_all.index[-1]}")
    return df_all


def detect_and_refill_gaps(exchange, df1m: pd.DataFrame, max_gaps_to_fix: int = 5) -> pd.DataFrame:
    """FIX (revision externa, oct-2026): fetch_ohlcv_paginated se para en
    cuanto el exchange devuelve un lote vacio -- si eso pasa por un hipo
    temporal de KuCoin a mitad de una descarga (no por haber llegado al
    presente), el hueco quedaba permanente: la siguiente ejecucion retoma
    desde la ultima vela ya en cache, sin volver a intentar ese tramo nunca.
    Esto detecta huecos reales (minutos consecutivos con un salto >1 minuto)
    y reintenta rellenarlos activamente antes de devolver el frame."""
    if len(df1m) < 2:
        return df1m

    gaps = df1m.index.to_series().diff()
    huecos = gaps[gaps > pd.Timedelta(minutes=1)]

    if len(huecos) == 0:
        return df1m

    if len(huecos) > max_gaps_to_fix:
        log.warning(f"Se detectaron {len(huecos)} huecos en la cache -- son demasiados para "
                    f"rellenar automaticamente en un solo ciclo (limite {max_gaps_to_fix}). "
                    f"Revisar la cache a mano.")
        huecos = huecos.iloc[:max_gaps_to_fix]

    for fin_hueco, duracion in huecos.items():
        inicio_hueco = fin_hueco - duracion
        log.warning(f"Hueco detectado en la cache: {inicio_hueco} -> {fin_hueco} "
                    f"({duracion}). Reintentando descargar ese tramo...")
        since_ms = int(inicio_hueco.timestamp() * 1000)
        rows = fetch_ohlcv_paginated(exchange, SYMBOL, "1m", since_ms)
        if not rows:
            log.warning(f"No se pudo rellenar el hueco {inicio_hueco} -> {fin_hueco} "
                        f"(el exchange tampoco devolvio datos ahora). Se reintentara en el proximo ciclo.")
            continue
        df_relleno = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df_relleno["ts"] = pd.to_datetime(df_relleno["ts"], unit="ms", utc=True)
        df_relleno = df_relleno.set_index("ts")
        df_relleno = df_relleno[df_relleno.index < fin_hueco]  # no duplicar lo que ya habia despues del hueco
        if len(df_relleno) > 0:
            df1m = pd.concat([df1m, df_relleno])
            df1m = df1m[~df1m.index.duplicated(keep="last")].sort_index()
            log.info(f"Hueco {inicio_hueco} -> {fin_hueco} rellenado con {len(df_relleno)} velas.")

    return df1m


# ══════════════════════════════════════════════════════════════════════════
# SEÑAL (copiado LITERAL de paper_trading_squeeze_system.py -- no reimplementar,
# copiar y pegar cualquier futura correccion desde alli para que no diverjan)
# ══════════════════════════════════════════════════════════════════════════

def compute_daily_regime(df1m: pd.DataFrame) -> pd.DataFrame:
    """NOTA DE NOMBRE y FIX de hueco de datos (revision externa, oct-2026,
    sincronizado con btc_trend_squeeze_system.py): la formula de vol_pctl
    selecciona volatilidad reciente ELEVADA, no compresion -- mantenida a
    proposito por compatibilidad con los resultados ya validados (ver el
    docstring completo en btc_trend_squeeze_system.py). Reindexa a un rango
    diario completo y avisa fuerte si hay NaN en los dias recientes (hueco
    de datos silencioso -- esto es lo que mas importa aqui, al ser el
    archivo que maneja dinero real)."""
    daily = df1m.resample("1D").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
    )
    full_range = pd.date_range(daily.index.min(), daily.index.max(), freq="1D", tz=daily.index.tz)
    daily = daily.reindex(full_range)

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

    dias_con_hueco_reciente = daily["close"].tail(SQUEEZE_LOOKBACK_DAYS + VOL_WINDOW_DAYS).isna()
    if dias_con_hueco_reciente.any():
        fechas_hueco = dias_con_hueco_reciente[dias_con_hueco_reciente].index
        log.warning(f"AVISO CRITICO: hueco de datos en {len(fechas_hueco)} dia(s) reciente(s): "
                    f"{list(fechas_hueco.strftime('%Y-%m-%d'))}. El regimen puede estar "
                    f"desactivado por falta de datos, no por ausencia real de señal.")

    return pd.DataFrame({"donch_upper": donch_upper, "donch_lower": donch_lower, "was_squeeze": was_squeeze})


def compute_15m_context(df1m: pd.DataFrame, daily_regime: pd.DataFrame) -> pd.DataFrame:
    df15 = df1m.resample("15min").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
    ).dropna()

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
    # fix (revision externa, oct-2026): reindexar cada columna por separado
    # con su tipo explicito, en vez de .values sobre la combinacion float+bool
    # (que fuerza dtype=object y podria leer un NaN futuro como verdadero).
    regime_mapped = daily_regime.reindex(df15["day"])
    df15["donch_upper"] = regime_mapped["donch_upper"].astype(float).values
    df15["donch_lower"] = regime_mapped["donch_lower"].astype(float).values
    df15["was_squeeze"] = regime_mapped["was_squeeze"].fillna(False).astype(bool).values
    return df15


def check_for_new_entry(df1m: pd.DataFrame, entries_today: int, last_entry_day: str, last_processed_bar: str):
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
        return None
    if pd.isna(last["donch_upper"]) or pd.isna(last["atr"]) or last["atr"] <= 0:
        return None
    if entries_today_actual >= MAX_ENTRIES_PER_DAY:
        return None
    if not last["was_squeeze"]:
        return None
    if pd.isna(last["vol_avg"]) or last["vol_avg"] <= 0:
        return None

    podria_romper = (last["high"] > last["donch_upper"]) or (last["low"] < last["donch_lower"])
    if not podria_romper:
        return None

    bar_start = df15.index[-1]
    bar_end = bar_start + pd.Timedelta(minutes=15)
    minute_bars = df1m.loc[bar_start:bar_end - pd.Timedelta(minutes=1)]
    if len(minute_bars) == 0:
        return None

    # fix (revision externa, oct-2026): el veto de "vela ambigua" usaba el
    # high/low de TODA la vela de 15m (solo conocidos al cerrarla) para una
    # decision cuyo fill se databa en un minuto anterior -- usaba informacion
    # del futuro. Ahora se usa el maximo/minimo ACUMULADO minuto a minuto
    # (causal) y se compara en que minuto se confirma cada direccion.
    running_high = minute_bars["high"].cummax().values
    running_low = minute_bars["low"].cummin().values
    vol_vals = minute_bars["volume"].values
    minutos = np.arange(1, len(minute_bars) + 1)
    cum_vol_prior = np.concatenate([[0.0], np.cumsum(vol_vals)[:-1]])
    minutos_prior = minutos - 1
    umbral = VOL_CONFIRM_MULT * last["vol_avg"] * (minutos_prior / 15)
    vol_ok = (cum_vol_prior >= umbral) & (minutos_prior >= 1)

    cross_up = (running_high > last["donch_upper"]) & vol_ok
    cross_down = (running_low < last["donch_lower"]) & vol_ok
    first_up = cross_up.argmax() if cross_up.any() else None
    first_down = cross_down.argmax() if cross_down.any() else None

    direction = None
    trig_idx = None
    if first_up is not None and first_down is not None:
        if first_up < first_down:
            direction, trig_idx = 1, first_up
        elif first_down < first_up:
            direction, trig_idx = -1, first_down
    elif first_up is not None:
        direction, trig_idx = 1, first_up
    elif first_down is not None:
        direction, trig_idx = -1, first_down

    if direction is None:
        return None

    level = last["donch_upper"] if direction == 1 else last["donch_lower"]
    stop_dist = max(MIN_STOP_PCT * level, ATR_MULT * last["atr"])
    entry_time = minute_bars.index[trig_idx]
    entry_bar = minute_bars.iloc[trig_idx]
    stop_price = level - stop_dist if direction == 1 else level + stop_dist

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
# CUENTA Y EJECUCION REAL
# ══════════════════════════════════════════════════════════════════════════

def fetch_real_balance(exchange):
    """Equity real de la cuenta de Futures en BTC (colateral COIN-M).

    Devuelve (balance_btc, descripcion_del_intento_que_funciono). En COIN-M el
    balance viene en BTC (XBT en la API de KuCoin). Como no he podido
    verificar como lo expone ccxt, se prueban varias formas de pedirlo y se
    usa la primera que devuelva un valor; --test-connection imprime cual
    funciono. Usa 'total' (equity) y cae a 'free' si no existe."""
    attempts = [{"currency": "XBT"}, {"code": "BTC"}, {}]
    last_err = None
    for params in attempts:
        try:
            bal = exchange.fetch_balance(params)
        except Exception as e:
            last_err = e
            continue
        entry = bal.get("BTC") or bal.get("XBT")
        if entry:
            value = entry.get("total") if entry.get("total") is not None else entry.get("free")
            if value is not None:
                return float(value), f"fetch_balance({params})"
    raise RuntimeError(
        f"No se pudo leer el balance en BTC de la cuenta de Futures (ultimo error: {last_err}). "
        f"Comprueba que tienes BTC transferido a la cuenta de Futures y ejecuta --test-connection."
    )


def fetch_real_position(exchange):
    """Posicion abierta real en el exchange para SYMBOL, o None si esta plano.
    Se usa para reconciliar contra el estado local -- nunca confiar solo en
    el JSON local en trading real."""
    try:
        positions = exchange.fetch_positions([SYMBOL])
    except Exception as e:
        log.error(f"No se pudo leer la posicion real del exchange: {e}")
        raise
    for p in positions:
        contracts = p.get("contracts") or 0
        if contracts and abs(float(contracts)) > 0:
            side = 1 if p.get("side") == "long" else -1
            return dict(direction=side, contracts=abs(float(contracts)),
                        entry_price=float(p.get("entryPrice") or 0))
    return None


def inverse_return(entry_price: float, exit_price: float, direction: int) -> float:
    """Retorno EN BTC, por unidad de nocional, de una posicion en un contrato
    INVERSO (PnL en BTC = contratos * contractSize * (1/entrada - 1/salida)).
    Largo: 1 - entrada/salida.  Corto: entrada/salida - 1.
    Para los movimientos de este sistema (~0.5%) es casi identico al retorno en
    precio del backtest (p.ej. stop de -0.5% -> -0.5025% en BTC), pero no exacto."""
    ratio = entry_price / exit_price
    return (1 - ratio) if direction == 1 else (ratio - 1)


def compute_order_amount(exchange, notional_usd: float, price: float) -> float:
    """Convierte un nocional en USD al numero de CONTRATOS de la orden (COIN-M).

    En un contrato INVERSO cada contrato vale contractSize USD (se espera 1),
    asi que contratos = nocional_usd / contractSize; el precio NO interviene
    (a diferencia de USDT-M). Fail-safe: si el mercado no es inverso o el
    contractSize no es el esperado, devuelve 0.0 y NO se opera."""
    exchange.load_markets()
    market = exchange.market(SYMBOL)

    if not market.get("inverse"):
        log.error(f"{SYMBOL} no figura como contrato inverso en ccxt. Se omite la orden. "
                  f"Revisa SYMBOL y ejecuta --test-connection.")
        return 0.0

    contract_size = float(market.get("contractSize") or 0.0)
    if abs(contract_size - EXPECTED_INVERSE_CONTRACT_USD) > 1e-9:
        log.error(f"contractSize={contract_size} distinto del esperado "
                  f"({EXPECTED_INVERSE_CONTRACT_USD} USD/contrato). No se opera hasta que lo "
                  f"verifiques en KuCoin y ajustes EXPECTED_INVERSE_CONTRACT_USD.")
        return 0.0

    raw_contracts = notional_usd / contract_size
    amount = float(exchange.amount_to_precision(SYMBOL, raw_contracts))

    min_amount = ((market.get("limits") or {}).get("amount") or {}).get("min")
    if min_amount is not None and amount < float(min_amount):
        log.warning(f"Cantidad calculada ({amount} contratos) por debajo del minimo del "
                    f"exchange ({min_amount}). Se omite la orden.")
        return 0.0

    log.info(f"Sizing COIN-M: nocional={notional_usd:.2f} USD, contractSize={contract_size} USD "
             f"-> {amount} contratos (~{amount * contract_size:.2f} USD reales tras redondeo, "
             f"precio de referencia {price:.2f})")
    return amount


def place_market_order(exchange, side: str, amount: float, reduce_only: bool = False):
    """Coloca una orden de mercado REAL. side = 'buy' o 'sell'.
    Bajo DRY_RUN, no llama al exchange -- devuelve un fill simulado con el
    precio actual, y lo deja bien claro en el log."""
    if DRY_RUN:
        ticker = exchange.fetch_ticker(SYMBOL)
        fill_price = float(ticker["last"])
        log.warning(
            f"[DRY_RUN -- SIN ORDEN REAL] Se habria colocado: {side.upper()} {amount} "
            f"{SYMBOL} @ mercado (precio actual ~{fill_price:.2f}), reduce_only={reduce_only}"
        )
        return dict(price=fill_price, amount=amount, id="DRY_RUN", dry_run=True)

    params = {"reduceOnly": reduce_only} if reduce_only else {}
    order = exchange.create_order(SYMBOL, "market", side, amount, params=params)
    # el fill real puede tardar un instante en reflejarse; comprobar el order status
    time.sleep(1)
    try:
        order = exchange.fetch_order(order["id"], SYMBOL)
    except Exception:
        pass  # si falla la consulta de seguimiento, usamos lo que ya tenemos
    fill_price = float(order.get("average") or order.get("price") or 0)
    filled_amount = float(order.get("filled") or amount)
    log.info(f"ORDEN REAL EJECUTADA: {side.upper()} {filled_amount} {SYMBOL} @ {fill_price:.2f} "
             f"(id={order.get('id')})")
    return dict(price=fill_price, amount=filled_amount, id=order.get("id"), dry_run=False)


def reconcile_position(exchange, state: dict):
    """Compara la posicion real del exchange contra el estado local. Si no
    coinciden, PARA el script -- una discrepancia aqui es demasiado peligrosa
    para seguir automaticamente.

    En DRY_RUN las posiciones locales son SIMULADAS (no existen en el
    exchange), asi que la comparacion no tiene sentido y bloquearia el flujo
    tras la primera entrada simulada: se omite, avisando si hubiera una
    posicion real abierta en la cuenta (que este script no toca)."""
    real_pos = fetch_real_position(exchange)
    local_pos = state.get("position")

    if DRY_RUN:
        if real_pos is not None:
            log.warning(f"DRY_RUN: hay una posicion REAL abierta en la cuenta "
                        f"({'LARGO' if real_pos['direction']==1 else 'CORTO'}, {real_pos['contracts']} contratos). "
                        f"Este script no la toca en DRY_RUN.")
        return True

    if real_pos is None and local_pos is None:
        return True  # ambos de acuerdo: plano
    if real_pos is not None and local_pos is not None:
        if real_pos["direction"] == local_pos["direction"]:
            return True  # ambos de acuerdo: misma direccion abierta
        msg = (
            f"El exchange tiene una posicion "
            f"{'LARGO' if real_pos['direction']==1 else 'CORTO'} pero el estado local "
            f"dice {'LARGO' if local_pos['direction']==1 else 'CORTO'}. PARANDO -- revisar a mano."
        )
        log.error(f"RECONCILIACION FALLIDA: {msg}")
        notify("RECONCILIACION FALLIDA -- revisar YA", msg)
        return False

    msg = (
        f"{'El exchange tiene una posicion abierta pero el estado local dice que esta plano' if real_pos else 'El estado local dice que hay una posicion abierta pero el exchange esta plano'}. "
        f"PARANDO -- revisar a mano antes de volver a ejecutar."
    )
    log.error(f"RECONCILIACION FALLIDA: {msg}")
    notify("RECONCILIACION FALLIDA -- revisar YA", msg)
    return False


# ══════════════════════════════════════════════════════════════════════════
# ESTADO Y GESTION DE RIESGO
# ══════════════════════════════════════════════════════════════════════════

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            state = json.load(f)
        state.setdefault("last_processed_bar", None)
        return state
    return dict(position=None, entries_today=0, last_entry_day=None, last_processed_bar=None)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def position_size_multiplier(stop_pct, equity_now, peak_equity):
    dd = equity_now / peak_equity - 1
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


def send_email_notification(subject: str, body: str):
    """Envia un email de aviso. Configuracion por variables de entorno (ver
    cabecera del script) -- si no estan puestas, no hace nada (no rompe el
    ciclo de trading). Un fallo al enviar el email TAMPOCO rompe el ciclo --
    se registra como aviso y se sigue."""
    smtp_host = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
    smtp_user = os.environ.get("EMAIL_SMTP_USER")
    smtp_password = os.environ.get("EMAIL_SMTP_PASSWORD")
    email_to = os.environ.get("EMAIL_TO")

    if not (smtp_user and smtp_password and email_to):
        return  # notificaciones no configuradas, silencioso a proposito

    try:
        import smtplib
        from email.mime.text import MIMEText

        prefijo = "[DRY_RUN] " if DRY_RUN else "[REAL] "
        msg = MIMEText(body)
        msg["Subject"] = f"{prefijo}BTC Squeeze Bot -- {subject}"
        msg["From"] = smtp_user
        msg["To"] = email_to

        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        log.info(f"Email de notificacion enviado: {subject}")
    except Exception as e:
        log.warning(f"No se pudo enviar el email de notificacion ({subject}): {e}")


def send_telegram_notification(subject: str, body: str):
    """Envia un mensaje de Telegram. Configuracion por variables de entorno
    (ver cabecera) -- si no estan puestas, no hace nada. Un fallo al enviar
    TAMPOCO rompe el ciclo de trading -- se registra como aviso y se sigue.
    Solo usa la libreria estandar (urllib), no hace falta instalar nada."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not (bot_token and chat_id):
        return

    try:
        import urllib.request
        import urllib.parse

        prefijo = "[DRY_RUN] " if DRY_RUN else "[REAL] "
        text = f"{prefijo}BTC Squeeze Bot — {subject}\n\n{body}"
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        log.info(f"Telegram de notificacion enviado: {subject}")
    except Exception as e:
        log.warning(f"No se pudo enviar el Telegram de notificacion ({subject}): {e}")


def notify(subject: str, body: str):
    """Punto unico de aviso -- manda por los canales que esten configurados
    (email, Telegram, ambos, o ninguno). Usar SIEMPRE esta funcion en vez de
    llamar a los canales por separado."""
    send_email_notification(subject, body)
    send_telegram_notification(subject, body)


# ══════════════════════════════════════════════════════════════════════════
# CICLO PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════

def run_once():
    exchange = get_exchange()
    df1m = update_data_cache(exchange)
    state = load_state()

    # el balance se lee del exchange cada vez -- en real no tiene sentido
    # llevar un "equity normalizado" aparte, la fuente de verdad es la
    # cuenta real. En COIN-M el equity esta en BTC.
    sin_credenciales = DRY_RUN and not os.environ.get("KUCOIN_API_KEY")
    if sin_credenciales:
        balance_btc = 0.1
        peak_balance_btc = state.get("peak_balance_btc", balance_btc)
        log.warning("Sin credenciales configuradas -- usando balance ficticio de 0.1 BTC solo para probar el flujo.")
    else:
        balance_btc, _ = fetch_real_balance(exchange)
        # fix (revision externa, oct-2026): el pico usado por el circuit
        # breaker SOLO se actualiza cuando estamos PLANOS (sin posicion
        # abierta). `fetch_real_balance` devuelve el balance TOTAL de la
        # cuenta, que en KuCoin COIN-M incluye el PnL NO REALIZADO de
        # cualquier posicion abierta -- si se actualizara el pico tambien
        # con una posicion abierta, una ganancia flotante inflaria el pico,
        # y si esa ganancia luego se reduce (aunque el trade cierre en
        # positivo o con una perdida pequeña), la comparacion contra ese
        # pico inflado podria disparar el freno de emergencia sin que
        # realmente se haya perdido el -8% en terminos realizados. El
        # circuit breaker solo se evalua de todas formas cuando estamos
        # planos (justo antes de buscar una entrada nueva), asi que esto no
        # pierde ninguna proteccion real.
        if state["position"] is None:
            peak_balance_btc = max(state.get("peak_balance_btc", balance_btc), balance_btc)
            state["peak_balance_btc"] = peak_balance_btc
        else:
            peak_balance_btc = state.get("peak_balance_btc", balance_btc)

    if not sin_credenciales and not reconcile_position(exchange, state):
        return  # discrepancia detectada, no seguir

    if state["position"] is not None:
        exit_info, new_stop = check_open_position_stop(df1m, state["position"])
        state["position"]["current_stop"] = new_stop

        if exit_info is not None:
            pos = state["position"]
            direction = pos["direction"]
            close_side = "sell" if direction == 1 else "buy"

            fill = place_market_order(exchange, close_side, pos["amount"], reduce_only=True)
            exit_price_real = fill["price"]
            costs = 2 * COST_BPS / 10000
            # retorno en precio (comparable con el backtest) y retorno real en BTC (inverso)
            ret_price = (exit_price_real / pos["entry_fill_price"] - 1) * direction - costs
            ret_btc = inverse_return(pos["entry_fill_price"], exit_price_real, direction) - costs

            # slippage de salida: fill real vs nivel TEORICO del stop (corregido:
            # antes se comparaba por error contra el nivel Donchian de entrada)
            stop_teorico = exit_info["exit_price"]
            slippage_exit = ((exit_price_real - stop_teorico) / stop_teorico) * (-direction)

            log.info(
                f"CIERRE {'LARGO' if direction==1 else 'CORTO'}: entrada_fill={pos['entry_fill_price']:.2f} "
                f"salida_fill={exit_price_real:.2f} stop_teorico={stop_teorico:.2f} "
                f"ret_precio={ret_price*100:.2f}% ret_BTC={ret_btc*100:.2f}% "
                f"slippage_salida={slippage_exit*100:+.3f}%"
            )
            notify(
                f"Cierre {'LARGO' if direction==1 else 'CORTO'} -- ret {ret_price*100:+.2f}%",
                f"Entrada: {pos['entry_fill_price']:.2f}\n"
                f"Salida real: {exit_price_real:.2f}  (stop teorico: {stop_teorico:.2f})\n"
                f"Retorno en precio: {ret_price*100:+.2f}%\n"
                f"Retorno en BTC: {ret_btc*100:+.2f}%\n"
                f"Slippage de salida: {slippage_exit*100:+.3f}%\n"
                f"Contratos: {pos['amount']}\n"
                f"Orden: {fill['id']}",
            )
            log_trade_to_csv(dict(
                entry_time=pos["entry_time"], exit_time=str(exit_info["exit_time"]),
                direction=direction, donch_level_entry=pos["donch_level"],
                entry_fill_price=pos["entry_fill_price"], exit_theoretical=stop_teorico,
                exit_fill_price=exit_price_real, ret_price_pct=ret_price * 100,
                ret_btc_pct_unlevered=ret_btc * 100, slippage_entry_pct=pos.get("slippage_entry"),
                slippage_exit_pct=slippage_exit, contracts=pos["amount"],
                balance_btc_at_entry=pos.get("balance_btc_at_entry"),
                order_id_exit=fill["id"], dry_run=fill["dry_run"],
            ))
            state["position"] = None
        else:
            log.info(
                f"Posicion abierta ({'LARGO' if state['position']['direction']==1 else 'CORTO'}), "
                f"stop actual={new_stop:.2f}, sigue viva."
            )
        save_state(state)
        return

    dd_actual = balance_btc / peak_balance_btc - 1
    if dd_actual <= -HALT_DD_THRESHOLD:
        log.warning(
            f"CIRCUIT BREAKER ACTIVO: drawdown actual={dd_actual*100:.1f}% <= "
            f"-{HALT_DD_THRESHOLD*100:.0f}%. No se buscan nuevas entradas hasta revision manual."
        )
        notify(
            "CIRCUIT BREAKER ACTIVADO",
            f"Drawdown actual: {dd_actual*100:.1f}%  (umbral: -{HALT_DD_THRESHOLD*100:.0f}%)\n"
            f"No se abriran nuevas posiciones hasta revision manual.",
        )
        return

    signal = check_for_new_entry(df1m, state["entries_today"], state["last_entry_day"], state["last_processed_bar"])
    if signal is None:
        log.info("Sin señal nueva. Sistema en espera.")
        return

    state["last_processed_bar"] = signal["bar_ts"]

    if signal["day"] != state["last_entry_day"]:
        state["entries_today"] = 0
    state["entries_today"] += 1
    state["last_entry_day"] = signal["day"]

    if signal["stopped_immediately"]:
        log.warning(
            "Señal con stop en la misma vela detectada -- por prudencia, en REAL no se "
            "coloca esta entrada (evita colocar y cerrar una orden real casi "
            "instantaneamente, que generaria coste sin capturar nada). Se registra "
            "como descartada y se pasa a la siguiente comprobacion."
        )
        save_state(state)
        return

    pos_mult, halted = position_size_multiplier(signal["stop_pct"], balance_btc, peak_balance_btc)
    if halted or pos_mult <= 0:
        log.warning("Circuit breaker activo en el momento de la señal -- no se abre posicion.")
        save_state(state)
        return

    # COIN-M: el equity esta en BTC, el nocional de la posicion se expresa en USD
    # (cada contrato = 1 USD): nocional_usd = pos_mult * equity_BTC * precio
    ticker = exchange.fetch_ticker(SYMBOL)
    current_price = float(ticker["last"])
    notional_usd = pos_mult * balance_btc * current_price
    amount = compute_order_amount(exchange, notional_usd, current_price)

    if amount <= 0:
        log.warning("Cantidad calculada para la orden es 0 (nocional demasiado pequeño, o "
                    "comprobacion de seguridad del contrato fallida) -- se omite.")
        save_state(state)
        return

    entry_side = "buy" if signal["direction"] == 1 else "sell"
    log.info(
        f"SEÑAL DE ENTRADA {'LARGO' if signal['direction']==1 else 'CORTO'}: nivel Donchian "
        f"(teorico)={signal['donch_level']:.2f}. Equity={balance_btc:.6f} BTC (~{balance_btc*current_price:.2f} USD), "
        f"posicion={pos_mult:.2f}x. Colocando orden de {amount} contratos {SYMBOL}..."
    )
    fill = place_market_order(exchange, entry_side, amount)

    slippage_entry = ((fill["price"] - signal["donch_level"]) / signal["donch_level"]) * signal["direction"]

    state["position"] = dict(
        entry_time=str(signal["entry_time"]), direction=signal["direction"],
        donch_level=signal["donch_level"],
        current_stop=signal["stop_price"], stop_dist=signal["stop_dist"],
        stop_pct=signal["stop_pct"], entry_fill_price=fill["price"], amount=fill["amount"],
        order_id_entry=fill["id"], slippage_entry=slippage_entry,
        balance_btc_at_entry=balance_btc,
    )

    log.info(
        f"ENTRADA {'LARGO' if signal['direction']==1 else 'CORTO'} ABIERTA: "
        f"nivel teorico={signal['donch_level']:.2f}  fill real={fill['price']:.2f}  "
        f"slippage={slippage_entry*100:+.3f}%  contratos={fill['amount']}  "
        f"entrada #{state['entries_today']} del dia."
    )
    notify(
        f"Entrada {'LARGO' if signal['direction']==1 else 'CORTO'} abierta",
        f"Nivel teorico (Donchian): {signal['donch_level']:.2f}\n"
        f"Fill real: {fill['price']:.2f}\n"
        f"Slippage de entrada: {slippage_entry*100:+.3f}%\n"
        f"Contratos: {fill['amount']}\n"
        f"Stop inicial: {signal['stop_price']:.2f}\n"
        f"Entrada #{state['entries_today']} del dia\n"
        f"Orden: {fill['id']}",
    )
    save_state(state)


def test_connection():
    """Comprobacion de solo lectura: verifica que las credenciales funcionan,
    sin tocar ordenes ni posiciones. Seguro de ejecutar en cualquier momento."""
    if ccxt is None:
        sys.exit("Falta ccxt. Instalar con: pip install ccxt --break-system-packages")

    api_key = os.environ.get("KUCOIN_API_KEY")
    api_secret = os.environ.get("KUCOIN_API_SECRET")
    api_password = os.environ.get("KUCOIN_API_PASSWORD")

    if not (api_key and api_secret and api_password):
        print("FALTAN credenciales. Comprueba que has hecho 'source /root/.kucoin_env' "
              "en esta misma sesion de terminal antes de ejecutar el script.")
        return

    exchange_class = getattr(ccxt, EXCHANGE_ID)
    exchange = exchange_class({
        "enableRateLimit": True,
        "apiKey": api_key, "secret": api_secret, "password": api_password,
    })

    print(f"Conectando a {EXCHANGE_ID}...")
    try:
        balance_btc, via = fetch_real_balance(exchange)
        print(f"OK -- conexion correcta. Equity en la cuenta de Futures: {balance_btc:.8f} BTC (leido con {via})")
    except Exception as e:
        print(f"FALLO al leer balance en BTC: {e}")
        print("Si tu cuenta de Futures no tiene BTC (solo USDT), COIN-M no funcionara: "
              "hay que transferir BTC a la cuenta de Futures.")
        return

    try:
        positions = exchange.fetch_positions([SYMBOL])
        abiertas = [p for p in positions if (p.get("contracts") or 0) not in (0, None)]
        if abiertas:
            print(f"AVISO: hay {len(abiertas)} posicion(es) abierta(s) en {SYMBOL} ahora mismo.")
        else:
            print(f"OK -- sin posiciones abiertas en {SYMBOL}.")
    except Exception as e:
        print(f"FALLO al leer posiciones: {e}")
        return

    try:
        exchange.load_markets()
        market = exchange.market(SYMBOL)
        contract_size = market.get("contractSize")
        min_amount = ((market.get("limits") or {}).get("amount") or {}).get("min")
        es_inverso = bool(market.get("inverse"))
        print(f"\nEspecificaciones del contrato {SYMBOL}:")
        print(f"  tipo: {'inverso (COIN-M)' if es_inverso else 'lineal (USDT-M)' if market.get('linear') else '?'}")
        print(f"  moneda de liquidacion: {market.get('settle')}")
        print(f"  contractSize: {contract_size} (en un inverso se espera {EXPECTED_INVERSE_CONTRACT_USD} USD por contrato)")
        print(f"  cantidad minima: {min_amount} contratos")
        ticker = exchange.fetch_ticker(SYMBOL)
        px = float(ticker["last"])
        equity_usd = balance_btc * px
        print(f"  precio actual: {px:.2f}   equity ~ {equity_usd:.2f} USD")
        for mult in (1.0, 2.0):
            notional = mult * equity_usd
            contratos = notional / float(contract_size) if contract_size else float("nan")
            print(f"  ejemplo: posicion de {mult:.0f}x tu equity -> {notional:.2f} USD -> {contratos:.0f} contratos")
        if not es_inverso:
            print("  AVISO: este mercado NO figura como inverso. El script esta pensado para COIN-M.")
        elif contract_size is None or abs(float(contract_size) - EXPECTED_INVERSE_CONTRACT_USD) > 1e-9:
            print("  AVISO: el contractSize no coincide con el esperado. El script NO operara hasta que lo verifiques en KuCoin.")
    except Exception as e:
        print(f"FALLO al leer las especificaciones del mercado: {e}")
        return

    print("\nTodo correcto si los datos de arriba coinciden con lo que ves en KuCoin Futures.")


def test_notify():
    """Envia una notificacion de prueba por cada canal que este configurado,
    y avisa claramente de cuales estan activos y cuales no. No toca el
    exchange ni el estado -- seguro de ejecutar en cualquier momento."""
    email_ok = bool(os.environ.get("EMAIL_SMTP_USER") and os.environ.get("EMAIL_SMTP_PASSWORD")
                     and os.environ.get("EMAIL_TO"))
    telegram_ok = bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))

    print(f"Email configurado:    {'si' if email_ok else 'NO'}")
    print(f"Telegram configurado: {'si' if telegram_ok else 'NO'}")

    if not email_ok and not telegram_ok:
        print("\nNo hay ningun canal configurado -- revisa que hayas hecho "
              "'source /root/.kucoin_env' en esta sesion de terminal, y que "
              "las variables esten bien escritas (ver cabecera del script).")
        return

    print("\nEnviando mensaje de prueba por cada canal configurado...")
    notify("Mensaje de prueba", "Si ves esto, el canal de notificaciones funciona correctamente.")
    print("\nRevisa tu email y/o Telegram ahora. Si no llega nada en un minuto o dos:")
    print("  - Revisa el log de arriba en esta misma terminal -- cualquier fallo de "
          "conexion o credenciales aparece ahi con el motivo exacto.")
    print("  - Para Gmail: confirma que usas una 'contraseña de aplicacion', no tu "
          "contraseña normal.")
    print("  - Para Telegram: confirma que le escribiste un mensaje al bot al menos "
          "una vez antes de sacar el chat_id (si no, el bot no puede escribirte primero).")


def main():
    parser = argparse.ArgumentParser(description="Trading REAL del sistema BTC Volatility Squeeze Breakout")
    parser.add_argument("--once", action="store_true", help="Ejecuta una sola comprobacion y sale (para cron)")
    parser.add_argument("--loop", action="store_true", help="Bucle propio, comprueba cada 60s")
    parser.add_argument(
        "--test-connection", action="store_true",
        help="Solo comprueba que las credenciales funcionan (lee balance y posiciones, "
             "no coloca ninguna orden). Usar esto primero, antes de nada mas.",
    )
    parser.add_argument(
        "--test-notify", action="store_true",
        help="Envia una notificacion de prueba por email/Telegram (los que esten "
             "configurados) y sale. No toca el exchange ni el estado.",
    )
    args = parser.parse_args()

    if args.test_connection:
        test_connection()
        return

    if args.test_notify:
        test_notify()
        return

    if not args.once and not args.loop:
        print("Especifica --once (para cron), --loop (bucle continuo), --test-connection "
              "o --test-notify. Ver --help.")
        return

    if DRY_RUN:
        log.info("=" * 70)
        log.info("MODO DRY_RUN ACTIVO -- no se colocara ninguna orden real.")
        log.info("=" * 70)
    else:
        log.warning("=" * 70)
        log.warning("MODO REAL ACTIVO -- se colocaran ordenes con dinero real.")
        log.warning("=" * 70)

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

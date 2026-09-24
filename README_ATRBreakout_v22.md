# ATR Breakout MNQ v2.2 — Documentación Técnica

## Descripción general

Sistema de momentum intradiario sobre MNQ (Micro E-mini Nasdaq-100 Futures, $2/punto).
Explota la persistencia del momentum intradiario documentada en Zarattini & Pagani (2026)
y Zarattini & Aziz (2023): cuando el precio supera significativamente el nivel de apertura
de la sesión, tiende a continuar en esa dirección el resto del día.

---

## 1. Construcción de features e indicadores

### Session Open (SO)
- Precio de apertura de la primera barra RTH (09:30 ET)
- Funciona como ancla del sistema y nivel de stop
- Se resetea cada día al inicio de la sesión RTH

### ATR(14) diario — causal
```python
atr14 = df_daily['close'].rolling(14).mean()  # ATR real con True Range
atr14_shifted = atr14.shift(1)                # shift(1) — usa el ATR de ayer
```
- Calculado sobre cierres diarios con el True Range estándar
- Se aplica `shift(1)` para garantizar causalidad: nunca usa datos del día actual
- Mínimo requerido: ATR > 1% del precio (filtra días de baja volatilidad)

### Bandas de breakout
```
banda_superior = SO + 0.50 × ATR(14)_ayer
banda_inferior = SO - 0.50 × ATR(14)_ayer
```

### Volatilidad para el sizing
```python
vol14 = returns_diarios.rolling(14).std().shift(1)  # vol realizada 14 días
vol5  = returns_diarios.rolling(5).std().shift(1)   # vol realizada 5 días
```
- Ambas con `shift(1)` para causalidad
- Ratio vol14/vol5 usado como scaling en el position sizing

### Filtro de rango de breakout (v2.2 — único cambio respecto a v2.1)
```python
rango_barra = high - low                    # rango de la barra de breakout
rango_ratio = rango_barra / ATR(14)_ayer    # normalizado por ATR
filtro_ok   = rango_ratio <= 0.50           # solo barras compactas
```
- Evidencia empírica: r = −0.167, p < 0.001 (Spearman, N=821 trades IS+OOS)
- Las barras de rango >0.50×ATR predicen trades peores (climax de volatilidad)
- Plateau robusto 0.40–0.80×ATR: no es un pico sobreoptimizado

---

## 2. Lógica exacta de entrada

### Slots temporales
Las señales solo se evalúan en los minutos :00, :15, :30 y :45 de cada hora.
Fuera de estos slots no se generan señales aunque el precio cruce la banda.

### Condición de breakout (en slot, dentro de RTH, sin posición abierta)
```
si ATR > 1% del precio:
    si rango_barra ≤ 0.50 × ATR:
        si close > banda_superior → señal_pendiente = LONG
        si close < banda_inferior → señal_pendiente = SHORT
```

### Confirmación por pullback
Una vez detectado el breakout, el sistema espera UNA barra de retroceso:
```
si señal_pendiente == LONG  y ret5_siguiente < 0 → ENTRAR LONG al close
si señal_pendiente == SHORT y ret5_siguiente > 0 → ENTRAR SHORT al close
```
- `ret5` = retorno de la barra de 5 minutos siguiente al breakout
- El pullback filtra breakouts de "ruido" que se deshacen inmediatamente
- Si no hay pullback, la señal se cancela en el EOD

### Horario operativo
- Solo dentro de RTH: 09:30 – 16:00 ET
- No se abren posiciones nuevas después de las 15:30 ET (zona EOD)

---

## 3. Lógica exacta de salida

### Stop Loss — Session Open (mecanismo near_stop)
El stop no es una orden límite sino un proceso de dos pasos:

**Paso 1 — Activación (near_stop = True):**
```
si LONG  y low_barra  ≤ SO → near_stop = True
si SHORT y high_barra ≥ SO → near_stop = True
```

**Paso 2 — Salida (cuando near_stop = True):**
```
si barra_favorable (ret5 en dirección del trade) → salir al close de esa barra
si barra_definitiva (close al otro lado del SO)   → salir al SO
```
- Razón: simula un stop-limit en lugar de stop-market
- Impacto cuantificado: +3.7pp CAGR vs salida inmediata al SO
- No es lookahead: la barra ya cerró antes de tomar la decisión

### Take Profit — EOD (End of Day)
```
si hora >= 15:30 ET y posición abierta → cerrar al close de la barra de 15:30
```
- No hay take profit de precio fijo
- El trade dura desde la entrada hasta las 15:30 o hasta el stop

### Cancelación de señal pendiente
```
si señal_pendiente != 0 y hora >= 15:30 → cancelar señal
```

---

## 4. Gestión de SL/TP y Position Sizing

### Sin take profit de precio
El sistema no cierra en profit antes del EOD. La única salida antes del cierre
es el stop en SO.

### Position Sizing — Volatility Targeting
```python
target_vol = 0.03  # 3% de volatilidad objetivo

# Scaling por régimen de volatilidad
vol_scale = clip(vol14 / vol5, 0.5, 1.5)

# Ajuste por nivel de precio (normaliza a 12,000 puntos de referencia)
price_adj = precio / 12_000

# N contratos
N = round(
    (target_vol × price_adj × vol_scale × equity) /
    (vol14 × precio × $2)
)
N = max(1, min(N_max, N))
```

### DD Scaling (reducción por drawdown)
```python
dd_from_peak = (peak - equity) / peak

si dd > 0.05:  scale *= max(0.5, 1 - (dd - 0.05) / 0.10)
si dd > 0.15:  scale = 0  # circuit breaker
```

### Circuit Breaker Mensual
```python
si retorno_mes < -8% → no operar el resto del mes
```

---

## 5. Tratamiento de comisiones y slippage

```python
COMISION  = 0.62  # $ por trade (round-trip NinjaTrader/IBKR)
SLIPPAGE  = 0.25  # puntos por lado (0.25 pts × $2 = $0.50)

# En cada trade:
pnl_neto = (exit_price - entry_price) × direccion × $2 × N - COMISION × N

# Slippage aplicado:
entry_price_real = close_barra + 0.25 × direccion   # peor precio de entrada
exit_price_real  = close_barra - 0.25 × direccion   # peor precio de salida
```

---

## 6. Separación temporal IS / OOS / Holdout

### Períodos
| Período | Años | Uso |
|---|---|---|
| In-Sample (IS) | 2019 – 2021 | Diseño y selección de filtros |
| Out-of-Sample (OOS) | 2022 – 2025 | Validación de cada hipótesis |
| Holdout | 2026 (hasta jul) | Solo para veredicto final — nunca para diseño |

### Protocolo de validación
1. Cualquier hipótesis se diagnostica primero en IS+OOS conjuntos (máxima muestra)
2. Si la señal es estadísticamente significativa (p < 0.05), se construye el filtro en IS
3. El filtro se prueba UNA SOLA VEZ en OOS con parámetros congelados
4. Si pasa OOS, se ejecuta walk-forward con ventanas deslizantes de 2 años IS + 1 año OOS
5. El holdout 2026 se usa exclusivamente como test final de la versión definitiva

### Walk-Forward
```
Ventana 1: IS=2019-2020  OOS=2021
Ventana 2: IS=2020-2021  OOS=2022
Ventana 3: IS=2021-2022  OOS=2023
Ventana 4: IS=2022-2023  OOS=2024
Ventana 5: IS=2023-2024  OOS=2025
```
Criterio de aceptación: Sharpe OOS > 1.0 en todas las ventanas, media > 1.5

### Hipótesis descartadas (no incorporadas a v2.2)
- Pullback 2 barras: −9.5pp CAGR
- Régimen ATR relativo: artefacto IS, −40pp OOS
- Slot horario: patrón IS revertido completamente en OOS
- Overnight gap: p=0.032 pero walk-forward negativo (−0.801 Sharpe)
- Eficiencia direccional ER1 (body/range): p=0.787, sin señal
- Volumen relativo: p=0.058, no significativo
- Streak de barras previas: p=0.789, ruido

---

## 7. Métricas de validación (OOS 2022-2025, capital $20k)

| Métrica | Valor |
|---|---|
| CAGR | +63.8% |
| Sharpe anualizado | 1.720 |
| Sortino | 2.891 |
| MDD | −18.2% |
| Win Rate | 56.8% |
| Profit Factor | 1.847 |
| Expectancy | +$163/trade |
| N medio contratos | 7.1 |
| Trades totales OOS | 524 |

### Holdout 2026 (2026-01-23 → 2026-07-29, 67 trades)
| Métrica | Valor |
|---|---|
| Sharpe | 1.602 |
| CAGR anualizado | +102% |
| MDD | −17.4% |
| Win Rate | 52.2% |

---

## 8. Archivos del proyecto

| Archivo | Descripción |
|---|---|
| `atr_breakout_v2_2.py` | Código completo de la estrategia y backtest |
| `mnq_atrbk_v22_all_trades_2019_2026.csv` | Todos los trades IS+OOS+Holdout |
| `ATRBreakoutMNQ.cs` | Implementación NinjaScript NT8 para live trading |
| `atr_breakout_v2_2_indicator.pine` | Indicador TradingView (Pine Script v6) |

---

## 9. Dependencias

```
Python >= 3.9
pandas >= 1.5
numpy >= 1.23
scipy >= 1.9
pyarrow (para leer parquet)
```

```bash
pip install pandas numpy scipy pyarrow
python atr_breakout_v2_2.py --data mnq_1min.parquet --capital 20000
```

---

## 10. Referencias académicas

- Zarattini & Pagani (2026): *Improving Performance with Fast Alphas*
- Zarattini & Aziz (2023): *Trading Strategies and Market Colour*
- Baltussen et al.: *Intraday Momentum across 60+ Futures Markets*

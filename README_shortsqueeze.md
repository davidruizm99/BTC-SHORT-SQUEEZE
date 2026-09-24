# BTC Volatility Squeeze Breakout — README

Sistema de ruptura direccional en BTC, filtrado por compresión previa de
volatilidad y confirmado por volumen, con reentrada intradía. Validado con
disciplina train/val/holdout estricta y auditado contra lookahead bias,
ambigüedad de ejecución intrabar y sensibilidad a slippage.

Código: `btc_trend_squeeze_system.py` (backtest) — `paper_trading_squeeze_system.py`
(versión en vivo, misma lógica de señal exacta, sincronizada).

---

## 1. Cómo se construyen los features/indicadores

Todos los indicadores usan `shift(1)` explícito — ningún valor usado en la
decisión de un periodo incluye información de ese mismo periodo.

**Régimen diario (recalculado cada día, UTC):**

| Indicador | Cálculo | Ventana |
|---|---|---|
| Canal Donchian | `max(high)` / `min(low)` de días previos | 5 días |
| Volatilidad realizada | `std(log-retornos diarios) × √365` | 20 días |
| Percentil de volatilidad | Percentil de la vol. de 20d dentro de su propio historial | 180 días |
| Squeeze activo | `True` si el percentil estuvo por debajo del 30% en cualquiera de los últimos N días | 14 días |

**Contexto de 15 minutos (para ejecución):**

| Indicador | Cálculo | Ventana |
|---|---|---|
| ATR | Media del True Range | 24h (96 barras de 15m), `shift(1)` |
| Volumen medio | Media del volumen por barra | 20 barras de 15m, `shift(1)` |

El régimen diario se mapea a cada barra de 15m por fecha (`day = index.normalize()`).

---

## 2. Lógica exacta de entrada y salida

**Condición de entrada (todas deben cumplirse):**
1. Squeeze activo (ver arriba)
2. La vela de 15m rompe el canal Donchian (por arriba → largo, por abajo → corto)
3. Se descarta si la vela rompe **ambas** direcciones a la vez (señal ambigua)
4. **Confirmación de volumen causal**: dentro de la vela de 15m donde ocurre la
   ruptura, se escanea minuto a minuto. El volumen acumulado se compara contra
   un umbral prorrateado (1.5× la media de 20 barras), pero **solo contando
   minutos estrictamente anteriores al minuto exacto del cruce** — nunca el
   volumen del propio minuto en curso (no sería causal: en el instante exacto
   del cruce, ese volumen aún no existe)

**Ejecución:** el fill teórico es el nivel exacto del canal Donchian, en el
minuto donde el precio lo cruza (resolución de 1 minuto, no de 15).

**Reentrada intradía:** hasta 3 intentos por día calendario (el original + 2
reentradas) mientras el squeeze siga activo. Probado explícitamente frente a
alternativas (tope por episodio de squeeze en vez de por día, sin tope) —
ninguna mejoró el día-calendario, se mantiene como está.

**Salida:** únicamente por stop dinámico (trailing) — no hay take-profit ni
cierre por tiempo. La posición corre hasta que el stop la cierra.

---

## 3. Gestión de SL/TP

**No hay take-profit.** Solo stop-loss dinámico (trailing).

**Stop inicial:**
```
distancia_stop = max(0.5% del precio de entrada, 0.5 × ATR de 24h)
```
En la práctica domina el suelo del 0.5% en la mayoría de los trades.

**Trailing — convención conservadora:** en cada barra de 1 minuto posterior a
la entrada, se **comprueba primero** si el precio ya tocó el stop vigente;
solo si no lo tocó, se actualiza el stop a favor de la posición usando el
máximo/mínimo de esa barra. (La convención contraria — actualizar primero y
comprobar después — es optimista y fue descartada tras auditoría.)

**Caso especial — stop en la misma vela de entrada:** si dentro del mismo
minuto de entrada el precio también toca el nivel del stop, la posición se
cierra ahí mismo (no se espera a la siguiente barra). Afecta a ~2.6% de las
entradas.

**Position sizing (capa de riesgo, separada de la señal):**
```
tamaño_posición = (riesgo_objetivo × equity) / distancia_stop_%
```
- Riesgo objetivo: 1.0% del equity por trade (fase funded) / 0.75% (challenge)
- Apalancamiento máximo: 5x
- De-risking escalonado: drawdown 5-8% → riesgo a la mitad; drawdown >8% →
  se detienen nuevas entradas (circuit breaker)

---

## 4. Tratamiento de comisiones y slippage

**Comisión fija en el backtest:** 5 bps por lado, aplicada tanto en la
entrada como en la salida (10 bps de coste de ida y vuelta total).

**Slippage: NO se asume ninguno adicional en el número de referencia del
backtest** — el fill teórico es el nivel exacto del canal Donchian. Esto es
una limitación conocida y explícitamente auditada, no un descuido:

| Escenario | Múltiplo de capital (periodo de validación) |
|---|---|
| Fill exacto + 5bps/lado (referencia del backtest) | ~8.5x |
| + slippage fijo 5-10bps adicional | 4.4x – 6.1x |
| **Punto de equilibrio** | **~15-25bps adicionales por lado** |
| + slippage 30bps+ adicional | Pérdida |

Es decir: **el sistema deja de tener edge si el slippage real efectivo supera
~20-30bps por lado.** Esto se está midiendo activamente en producción — la
versión de paper trading captura el precio real de mercado en el instante
exacto de cada señal (`real_price_at_entry`, `slippage_entry_pct` en el log
de trades) para comparar contra el fill teórico del backtest.

Una validación con datos de tick reales de Binance (10 eventos, 2025) mostró
liquidez alta (gaps entre trades de milisegundos) y slippage a 5 segundos
entre -0.075% y +0.059% — dentro del margen tolerable, pero con muestra
insuficiente para ser concluyente.

---

## 5. Train / Validation / Holdout

**Split temporal fijo, sin walk-forward:**

| Ventana | Periodo |
|---|---|
| TRAIN (IS) | Inicio de datos → 2022-12-31 |
| VAL (OOS) | 2023-01-01 → 2024-12-31 |
| HOLDOUT | 2025-01-01 → fin de datos |

El holdout se tocó una única vez por cada cambio de diseño ya confirmado en
TRAIN/VAL (no se optimizó ningún parámetro mirando el holdout). Se llevó un
registro explícito de cuántas veces se consultó el holdout a lo largo del
desarrollo (riesgo de *winner's curse* por comparaciones múltiples) — la
independencia estadística real se verificó agrupando trades por episodio de
squeeze en vez de por trade individual (~17 episodios independientes en 4
años de datos, no cientos de trades independientes).

**No hay walk-forward formal** (reentrenamiento periódico de parámetros) —
los parámetros son fijos desde el diseño inicial y se mantienen constantes en
todo el periodo. Queda como línea de trabajo pendiente, no implementada.

---

## Advertencias que hay que leer antes de asignar capital

El propio script (`btc_trend_squeeze_system.py`) documenta en su cabecera un
historial de auditoría de 6 rondas con los bugs reales encontrados y
corregidos (lookahead en el régimen, ambigüedad de ejecución a 15m resuelta
pasando a 1m, causalidad del volumen, etc.). **El punto abierto más
importante es el slippage real** — todo lo demás está verificado, esto
todavía no.

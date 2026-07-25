# Resultados multi-mercado — estrategia "Primera Vela"

Backtest ejecutado con la configuración por defecto del bot (reglas del
vídeo: rango de 5 min de la apertura de NY a las 09:30, ruptura por cierre en
1m, stop en el último swing de M1, objetivo 1:1, filtro de breakout exagerado
al 50% del rango, 1 operación/día, ventana de 90 min, caso conservador cuando
stop y objetivo caen en la misma vela, sin comisiones ni slippage).

Los datos son las **muestras gratuitas de 1 minuto** disponibles públicamente
(QuantConnect Lean `Data/`, testdata de freqtrade y ejemplos de mplfinance),
porque las APIs de mercado no eran accesibles desde el entorno donde se
ejecutó. Son pocas sesiones y de épocas distintas: sirven como prueba de
funcionamiento multi-mercado, **no** como validación estadística.

| Mercado | Datos | Sesiones operadas | Aciertos | R total | Profit factor | Salidas O/S/T |
|---|---|---:|---:|---:|---:|---|
| **Futuros ES** (S&P 500 E-mini, CME) | 22 días (2013-2020, Lean) | 14 | 71.4% | **+6.88 R** | 3.20 | 10/3/1 |
| **Acciones SPY** (NYSE) | 7 días (2013-2023, Lean) | 7 | 85.7% | **+5.00 R** | 6.00 | 6/1/0 |
| **Índice S&P 500** (1m, nov 2019) | 4 días (mplfinance) | 4 | 50.0% | 0.00 R | 1.00 | 2/2/0 |
| **Forex EURUSD** (Oanda, precio medio) | 17 días (2014-2018, Lean) | 13 | 46.2% | −1.00 R | 0.86 | 6/7/0 |
| **Cripto BTC/USD** (Coinbase) | 13 días (2016-2018, Lean) | 9 | 44.4% | −1.00 R | 0.80 | 4/5/0 |
| **Cripto BTC** (2017, freqtrade) | 11 días (nov 2017) | 10 | 30.0% | −3.98 R | 0.43 | 3/6/1 |
| **Futuros oro GC** (COMEX) | 8 días (Lean) | 0 | — | — | — | muestra demasiado dispersa: sin velas en 09:30-09:35 |

O/S/T = objetivo / stop / tiempo. Total agregado: **57 operaciones, +5.9 R**.

## Lectura de los resultados

- **Donde mejor funciona es exactamente donde el vídeo dice que funciona**:
  índices americanos con apertura real de sesión (futuros ES y SPY), que
  concentran volumen e impulso a las 09:30. Tasas de acierto del 71-86% con
  1:1, en línea con el ~65% que menciona el autor.
- **En cripto funciona peor** (30-44% de acierto): al ser un mercado 24/7 no
  existe una "apertura" con subasta y acumulación de órdenes, así que la
  primera vela de las 09:30 de NY tiene mucho menos significado estructural.
- **EURUSD queda al borde del breakeven**: el vídeo ya avisa de que en forex
  solo "algunos pares pueden funcionar".
- Muestras de 7-22 sesiones **no permiten conclusiones estadísticas**; el
  intervalo de confianza de una tasa de acierto con n=14 es enorme. Para la
  validación seria ("probada 1000 veces") hacen falta meses de datos de 1m:
  usa `python -m primera_vela.cli descargar` (yfinance, ~30 días) o un
  proveedor de históricos, y repite por mercado.
- Los resultados son **sin fricción**. Con 1:1, añadir comisión+slippage
  realistas (`--comision`, `--slippage`) resta varios puntos de tasa de
  acierto efectiva: repite el backtest con la fricción de tu bróker antes de
  decidir nada.

## Reproducir

```bash
# 1. Consigue las muestras de Lean (carpeta Data/ del repo de QuantConnect)
git clone --depth 1 --filter=blob:none --sparse https://github.com/QuantConnect/Lean
cd Lean && git sparse-checkout set Data/forex Data/crypto Data/future Data/equity/usa/minute

# 2. Convierte a CSV en hora de NY
python scripts/convertir_lean.py --lean-data Lean/Data --salida csv/

# 3. Backtest por mercado
python -m primera_vela.cli backtest --csv csv/es_futuro.csv --trades-csv trades_es.csv
python -m primera_vela.cli backtest --csv csv/eurusd.csv
python -m primera_vela.cli backtest --csv csv/btc_coinbase.csv
```

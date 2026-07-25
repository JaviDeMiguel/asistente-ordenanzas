# Bot de backtesting — Estrategia "Primera Vela" (ORB 5m/1m)

Implementación en Python de la estrategia del vídeo *"Mi sencilla estrategia de
trading de 5 minutos para la primera vela (probada 1000 veces)"*: un **Opening
Range Breakout** sobre la primera vela de 5 minutos de la apertura de Nueva
York, ejecutado en temporalidad de 1 minuto.

## Reglas de la estrategia (tal y como se describen en el vídeo)

1. **Paso 1 — El rango**: espera a que abra la sesión de Nueva York (09:30
   hora de NY) y deja que se desarrollen los primeros 5 minutos. Marca el
   **máximo** y el **mínimo** de esa primera vela de 5 minutos.
2. **Paso 2 — La señal**: baja a temporalidad de 1 minuto y espera a que una
   vela cierre **con el cuerpo** por encima del máximo (largo) o por debajo
   del mínimo (corto) del rango. La mecha no cuenta: lo que importa es el
   cierre.
3. **Paso 3 — Entrada, stop y objetivo** (entrada tipo *breakout*):
   - Entrada a mercado al cierre de la vela de ruptura (el bot la ejecuta en
     la apertura de la vela siguiente, que es el precio realmente operable).
   - **Stop loss**: detrás del último mínimo/máximo (estructura) en M1; si el
     mercado no dejó ningún pivote utilizable, el extremo opuesto del rango
     (el propio vídeo muestra que en ese caso el stop queda amplio).
   - **Objetivo**: Risk/Reward **1:1** — el autor lo justifica porque los
     stops de esta operativa tienden a ser amplios y buscar más recorrido
     baja mucho la efectividad.
   - **Filtro**: no entrar si el breakout es *exagerado* (el cierre queda
     demasiado lejos del nivel roto y el stop deja de estar "protegido"
     cerca del rango).
4. La estrategia busca movimientos rápidos tras la apertura: solo se opera en
   los **primeros 90 minutos** de la sesión; lo que siga abierto al final de
   la ventana se cierra a mercado. Una operación por día.
5. Mercado ideal según el vídeo: mercados impulsivos — futuros del Nasdaq
   (NQ/MNQ), oro, petróleo — aunque es aplicable a acciones, cripto y forex.
   Con R/R 1:1 el sistema necesita >50% de acierto; el vídeo habla de tasas
   en torno al 65% en mercados volátiles (verifícalo con tu propio backtest).

> El vídeo describe una segunda variante de entrada (impulso → retroceso →
> ruptura de la estructura) y recomienda elegir **una** de las dos para no
> sobreoperar. Este bot implementa la entrada por *breakout*, que es la que
> el autor usa en todos los ejemplos.

## Instalación

```bash
cd trading_bot
pip install -r requirements.txt
```

## Uso

### 1. Con tus propios datos (recomendado)

Cualquier CSV de velas de **1 minuto** con columnas
`timestamp,open,high,low,close[,volume]` (los nombres son flexibles; también
acepta `fecha`, `apertura`, `máximo`...). Si los timestamps no llevan zona
horaria se asumen en hora de Nueva York.

```bash
python -m primera_vela.cli backtest --csv datos_nq_1m.csv --trades-csv operaciones.csv
```

### 2. Descargando de Yahoo Finance

Yahoo solo ofrece ~30 días de histórico de 1 minuto, pero sirve para probar:

```bash
python -m primera_vela.cli descargar --simbolo NQ=F --dias 25 --salida nq.csv
python -m primera_vela.cli backtest --csv nq.csv
```

### 3. Sin conexión (datos sintéticos)

Genera sesiones artificiales reproducibles para validar el motor:

```bash
python -m primera_vela.cli backtest --sintetico 60
```

### Parámetros de la estrategia

| Opción | Por defecto | Descripción |
|---|---|---|
| `--apertura` | `09:30` | Hora de apertura de la sesión |
| `--zona-horaria` | `America/New_York` | Zona horaria de la sesión |
| `--rango-min` | `5` | Minutos de la "primera vela" |
| `--ventana-min` | `90` | Ventana operativa desde la apertura |
| `--risk-reward` | `1.0` | Objetivo como múltiplo del riesgo |
| `--max-breakout` | `0.5` | Filtro de breakout exagerado (fracción del rango); `-1` lo desactiva |
| `--swing-lookback` | `2` | Velas a cada lado para detectar el swing del stop |
| `--max-operaciones` | `1` | Operaciones máximas por sesión |
| `--solo-largos` / `--solo-cortos` | — | Restringe la dirección |
| `--comision` | `0` | Coste ida y vuelta en puntos |
| `--slippage` | `0` | Deslizamiento por fill en puntos |
| `--mismo-bar-optimista` | off | Si stop y objetivo caen en la misma vela de 1m, asumir objetivo (por defecto se asume stop, el caso conservador) |

El resumen incluye: operaciones, tasa de acierto, resultado total en **R**
(múltiplos de riesgo) y en puntos, profit factor, máximo drawdown en R y el
desglose de salidas (objetivo/stop/tiempo). Con `--trades-csv` se guarda el
registro completo de operaciones y con `--json` el resumen sale en JSON.

## Tests

```bash
python -m pytest tests/ -o addopts=""
```

## Notas y limitaciones honestas

- Con velas de 1 minuto no se conoce el orden de los ticks dentro de la vela:
  si el stop y el objetivo caen en la misma vela, el resultado es ambiguo. Por
  defecto se asume el **peor caso** (stop). Compara con
  `--mismo-bar-optimista` para acotar el resultado real entre ambos extremos.
- El "último swing en M1" del vídeo es discrecional; aquí se formaliza como un
  pivote fractal (`--swing-lookback`). Prueba valores 1-3.
- Añade `--comision` y `--slippage` realistas (en NQ, 0.5-1 punto de fricción
  total es razonable) antes de sacar conclusiones: las estrategias de scalping
  con R/R 1:1 son muy sensibles a la fricción.
- Backtesting ≠ garantía de resultados futuros. El propio vídeo recomienda
  hacer backtesting propio antes de operar en real.

"""Convierte datos de muestra de QuantConnect Lean a CSVs de 1 minuto en hora de NY.

Lean (https://github.com/QuantConnect/Lean) incluye en su carpeta `Data/`
muestras gratuitas de velas de 1 minuto de varios mercados (futuros ES,
forex, cripto, acciones). Este script las convierte al formato CSV que
consume `primera_vela.cli backtest --csv`.

Formato Lean: un zip por día con un CSV `ms_desde_medianoche,O,H,L,C[,V]`.
Las acciones van multiplicadas por 10000 y en hora de Nueva York; futuros,
forex y cripto van en UTC. Los ficheros *_quote.zip llevan bid y ask (se usa
el precio medio).

Uso:
    python scripts/convertir_lean.py --lean-data /ruta/a/Lean/Data --salida ./csv

    # luego, por ejemplo:
    python -m primera_vela.cli backtest --csv csv/es_futuro.csv
"""

from __future__ import annotations

import argparse
import glob
import os
import zipfile

import pandas as pd

NY = "America/New_York"


def leer_zips_lean(
    pattern: str, kind: str, scale: float = 1.0, tz: str = "UTC"
) -> pd.DataFrame:
    rows = []
    for z in sorted(glob.glob(pattern)):
        day = os.path.basename(z).split("_")[0]
        base = pd.Timestamp(day).tz_localize(tz)
        zf = zipfile.ZipFile(z)
        for line in zf.read(zf.namelist()[0]).decode().splitlines():
            p = line.split(",")
            if len(p) < 5:
                continue
            ts = base + pd.Timedelta(milliseconds=int(p[0]))
            if kind == "trade":
                o, h, l, c = (float(x) / scale for x in p[1:5])
                v = float(p[5]) if len(p) > 5 else 0.0
            else:  # quote: bid OHLC, tamaño, ask OHLC, tamaño -> precio medio
                bo, bh, bl, bc = (float(x) for x in p[1:5])
                ao, ah, al, ac = (float(x) for x in p[6:10])
                o, h, l, c = (bo + ao) / 2, (bh + ah) / 2, (bl + al) / 2, (bc + ac) / 2
                v = 0.0
            rows.append((ts, o, h, l, c, v))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    ).set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df.index = df.index.tz_convert(NY)
    return df


DATASETS = [
    # (nombre de salida, patrón relativo a Data/, tipo, escala, tz de origen)
    ("es_futuro", "future/cme/minute/es/*_trade.zip", "trade", 1.0, "UTC"),
    ("oro_futuro", "future/comex/minute/gc/*_trade.zip", "trade", 1.0, "UTC"),
    ("eurusd", "forex/oanda/minute/eurusd/*_quote.zip", "quote", 1.0, "UTC"),
    ("spy", "equity/usa/minute/spy/*_trade.zip", "trade", 10000.0, NY),
    ("btc_coinbase", "crypto/coinbase/minute/btcusd/*_trade.zip", "trade", 1.0, "UTC"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lean-data", required=True, help="Ruta a la carpeta Data/ de Lean")
    ap.add_argument("--salida", default=".", help="Directorio de salida de los CSV")
    args = ap.parse_args()

    os.makedirs(args.salida, exist_ok=True)
    for name, rel, kind, scale, tz in DATASETS:
        df = leer_zips_lean(os.path.join(args.lean_data, rel), kind, scale, tz)
        if df.empty:
            print(f"{name:14} sin datos ({rel})")
            continue
        out = os.path.join(args.salida, f"{name}.csv")
        df.to_csv(out, index_label="timestamp")
        dias = df.groupby(df.index.normalize()).size().size
        print(f"{name:14} {len(df):>7} velas  {dias:>3} dias  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

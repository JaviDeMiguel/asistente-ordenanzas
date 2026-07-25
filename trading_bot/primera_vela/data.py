"""Carga y generación de datos OHLCV de 1 minuto.

Tres fuentes:

- CSV con columnas timestamp/open/high/low/close/volume (nombres flexibles).
- Descarga vía yfinance (Yahoo limita el histórico de 1m a ~30 días).
- Generador sintético reproducible, útil para probar el motor sin conexión.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ["open", "high", "low", "close"]

_COLUMN_ALIASES = {
    "open": {"open", "o", "apertura"},
    "high": {"high", "h", "maximo", "máximo", "max"},
    "low": {"low", "l", "minimo", "mínimo", "min"},
    "close": {"close", "c", "cierre", "last"},
    "volume": {"volume", "vol", "v", "volumen"},
}

_TIME_CANDIDATES = ["timestamp", "datetime", "date", "time", "fecha", "hora"]


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    mapping: dict[str, str] = {}
    for col in df.columns:
        key = str(col).strip().lower()
        for canonical, aliases in _COLUMN_ALIASES.items():
            if key in aliases:
                mapping[col] = canonical
                break
    df = df.rename(columns=mapping)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas OHLC en el CSV: {missing}")
    if "volume" not in df.columns:
        df["volume"] = 0.0
    return df[REQUIRED_COLUMNS + ["volume"]].astype(float)


def load_csv(path: str, timezone: str = "America/New_York") -> pd.DataFrame:
    """Carga un CSV de velas de 1 minuto y lo devuelve indexado en `timezone`.

    Acepta timestamps con o sin zona horaria; los ingenuos se asumen ya en
    `timezone`.
    """
    raw = pd.read_csv(path)
    time_col = None
    for cand in _TIME_CANDIDATES:
        for col in raw.columns:
            if str(col).strip().lower() == cand:
                time_col = col
                break
        if time_col is not None:
            break
    if time_col is None:
        time_col = raw.columns[0]

    # Un CSV largo puede mezclar offsets (-04:00/-05:00) por el horario de
    # verano; en ese caso hay que parsear en UTC y convertir después.
    sample = pd.Timestamp(str(raw[time_col].dropna().iloc[0]))
    df = raw.drop(columns=[time_col])
    if sample.tz is None:
        index = pd.to_datetime(raw[time_col], format="mixed")
        df.index = pd.DatetimeIndex(index).tz_localize(timezone)
    else:
        index = pd.to_datetime(raw[time_col], utc=True, format="mixed")
        df.index = pd.DatetimeIndex(index).tz_convert(timezone)
    df = _normalize_columns(df)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def download_yfinance(
    symbol: str = "NQ=F",
    days: int = 25,
    timezone: str = "America/New_York",
) -> pd.DataFrame:
    """Descarga velas de 1 minuto con yfinance (máx. ~30 días de histórico).

    Símbolos útiles: NQ=F (futuro Nasdaq 100), MNQ=F (micro), ES=F (S&P 500),
    QQQ, SPY, BTC-USD...
    """
    import yfinance as yf  # importación diferida: dependencia opcional

    frames = []
    # Yahoo limita cada petición de 1m a 7 días.
    end = pd.Timestamp.now(tz="UTC").ceil("min")
    start_limit = end - pd.Timedelta(days=min(days, 29))
    cursor = end
    while cursor > start_limit:
        chunk_start = max(cursor - pd.Timedelta(days=7), start_limit)
        chunk = yf.download(
            symbol,
            start=chunk_start.to_pydatetime(),
            end=cursor.to_pydatetime(),
            interval="1m",
            progress=False,
            auto_adjust=False,
        )
        if chunk is not None and not chunk.empty:
            frames.append(chunk)
        cursor = chunk_start
    if not frames:
        raise RuntimeError(f"yfinance no devolvió datos para {symbol}")

    df = pd.concat(frames)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[REQUIRED_COLUMNS + ["volume"]]
    df.index = pd.DatetimeIndex(df.index).tz_convert(timezone)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df.astype(float)


def generate_synthetic(
    sessions: int = 30,
    seed: int = 7,
    timezone: str = "America/New_York",
    start_price: float = 20000.0,
) -> pd.DataFrame:
    """Genera sesiones sintéticas de 1 minuto (09:30-16:00) con apertura volátil.

    El proceso mezcla días con tendencia tras la apertura (donde el breakout
    del rango funciona) y días de rango, para que el backtest ejercite ambos
    resultados. Reproducible mediante `seed`.
    """
    rng = np.random.default_rng(seed)
    bars_per_session = 390  # 09:30 -> 16:00
    all_index: list[pd.Timestamp] = []
    opens, highs, lows, closes, vols = [], [], [], [], []

    price = start_price
    day = pd.Timestamp("2025-01-06", tz=timezone)  # lunes
    made = 0
    while made < sessions:
        if day.dayofweek < 5:
            session_start = day + pd.Timedelta(hours=9, minutes=30)
            trend = rng.choice([-1.0, 0.0, 1.0], p=[0.35, 0.3, 0.35])
            drift = trend * rng.uniform(0.3, 1.2)
            for i in range(bars_per_session):
                ts = session_start + pd.Timedelta(minutes=i)
                # Más volatilidad en los primeros 30 minutos.
                vol_scale = 3.0 if i < 30 else 1.0
                step = rng.normal(drift if 5 <= i <= 120 else 0.0, 4.0 * vol_scale)
                o = price
                c = price + step
                spread = abs(rng.normal(0, 3.0 * vol_scale))
                h = max(o, c) + spread
                l = min(o, c) - spread
                v = float(rng.integers(500, 5000) * vol_scale)
                all_index.append(ts)
                opens.append(o)
                highs.append(h)
                lows.append(l)
                closes.append(c)
                vols.append(v)
                price = c
            made += 1
        day += pd.Timedelta(days=1)

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
        index=pd.DatetimeIndex(all_index),
    )

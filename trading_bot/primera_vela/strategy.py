"""Lógica de señales de la estrategia "Primera Vela".

Paso 1: marcar máximo y mínimo de la primera vela de 5 minutos de la apertura
        de Nueva York.
Paso 2: esperar a que una vela de 1 minuto cierre por encima del máximo
        (largo) o por debajo del mínimo (corto).
Paso 3: entrada en la apertura de la vela siguiente, stop en el último
        swing de M1 y objetivo a Risk/Reward configurable (1:1 por defecto).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import StrategyConfig


@dataclass
class OpeningRange:
    high: float
    low: float
    start: pd.Timestamp
    end: pd.Timestamp

    @property
    def size(self) -> float:
        return self.high - self.low


@dataclass
class Signal:
    side: str  # "long" | "short"
    signal_time: pd.Timestamp  # cierre de la vela de ruptura
    entry_time: pd.Timestamp  # apertura de la vela siguiente
    entry_price: float
    stop_price: float
    target_price: float
    range_high: float
    range_low: float

    @property
    def risk(self) -> float:
        return abs(self.entry_price - self.stop_price)


def compute_opening_range(
    session: pd.DataFrame, config: StrategyConfig
) -> OpeningRange | None:
    """Máximo y mínimo de los primeros `opening_range_minutes` de la sesión."""
    day = session.index[0].normalize()
    start = day + pd.Timedelta(
        hours=config.session_open.hour, minutes=config.session_open.minute
    )
    end = start + pd.Timedelta(minutes=config.opening_range_minutes)
    window = session[(session.index >= start) & (session.index < end)]
    if window.empty:
        return None
    return OpeningRange(
        high=float(window["high"].max()),
        low=float(window["low"].min()),
        start=start,
        end=end,
    )


def last_swing_low(
    session: pd.DataFrame, before: pd.Timestamp, lookback: int
) -> float | None:
    """Último mínimo swing en M1 anterior a `before`.

    Un swing low es una vela cuyo mínimo es menor o igual que el de las
    `lookback` velas a cada lado (pivote fractal).
    """
    lows = session.loc[session.index < before, "low"]
    n = len(lows)
    for i in range(n - 1 - lookback, lookback - 1, -1):
        center = lows.iloc[i]
        left = lows.iloc[i - lookback : i]
        right = lows.iloc[i + 1 : i + 1 + lookback]
        if (center <= left).all() and (center <= right).all():
            return float(center)
    return None


def last_swing_high(
    session: pd.DataFrame, before: pd.Timestamp, lookback: int
) -> float | None:
    """Último máximo swing en M1 anterior a `before` (simétrico al swing low)."""
    highs = session.loc[session.index < before, "high"]
    n = len(highs)
    for i in range(n - 1 - lookback, lookback - 1, -1):
        center = highs.iloc[i]
        left = highs.iloc[i - lookback : i]
        right = highs.iloc[i + 1 : i + 1 + lookback]
        if (center >= left).all() and (center >= right).all():
            return float(center)
    return None


def find_signals(session: pd.DataFrame, config: StrategyConfig) -> list[Signal]:
    """Devuelve las señales de ruptura de una sesión, en orden temporal.

    `session` debe contener únicamente las velas de 1 minuto de un día de
    mercado, indexadas en la zona horaria de la sesión.
    """
    orange = compute_opening_range(session, config)
    if orange is None or orange.size <= 0:
        return []

    window_end = orange.start + pd.Timedelta(minutes=config.trading_window_minutes)
    candidates = session[(session.index >= orange.end) & (session.index < window_end)]

    signals: list[Signal] = []
    for ts, bar in candidates.iterrows():
        close = float(bar["close"])
        side = None
        level = None
        if config.allow_longs and close > orange.high:
            side, level = "long", orange.high
        elif config.allow_shorts and close < orange.low:
            side, level = "short", orange.low
        if side is None:
            continue

        # Filtro de "breakout exagerado": el cierre no debe alejarse del nivel
        # roto más de una fracción del tamaño del rango.
        if config.max_breakout_fraction is not None:
            distance = abs(close - level)
            if distance > config.max_breakout_fraction * orange.size:
                continue

        # La entrada se ejecuta en la apertura de la vela siguiente.
        later = session[session.index > ts]
        if later.empty or later.index[0] >= window_end:
            continue
        entry_time = later.index[0]
        entry_price = float(later.iloc[0]["open"])

        bar_close_time = entry_time  # los swings se buscan antes de la entrada
        if side == "long":
            stop = last_swing_low(session, bar_close_time, config.swing_lookback)
            if stop is None or stop >= entry_price:
                stop = orange.low
            if stop >= entry_price:
                continue
            risk = entry_price - stop
            target = entry_price + config.risk_reward * risk
        else:
            stop = last_swing_high(session, bar_close_time, config.swing_lookback)
            if stop is None or stop <= entry_price:
                stop = orange.high
            if stop <= entry_price:
                continue
            risk = stop - entry_price
            target = entry_price - config.risk_reward * risk

        signals.append(
            Signal(
                side=side,
                signal_time=ts,
                entry_time=entry_time,
                entry_price=entry_price,
                stop_price=float(stop),
                target_price=float(target),
                range_high=orange.high,
                range_low=orange.low,
            )
        )
    return signals

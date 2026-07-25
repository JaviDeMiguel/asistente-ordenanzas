"""Motor de backtesting de la estrategia "Primera Vela"."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .strategy import Signal, find_signals


@dataclass
class Trade:
    date: pd.Timestamp
    side: str
    entry_time: pd.Timestamp
    entry_price: float
    stop_price: float
    target_price: float
    exit_time: pd.Timestamp
    exit_price: float
    exit_reason: str  # "target" | "stop" | "time"
    points: float  # resultado bruto en puntos (ya con slippage/comisión)
    r_multiple: float  # resultado en múltiplos del riesgo inicial


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    sessions_total: int = 0
    sessions_with_range: int = 0

    def to_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(
                columns=[
                    "date", "side", "entry_time", "entry_price", "stop_price",
                    "target_price", "exit_time", "exit_price", "exit_reason",
                    "points", "r_multiple",
                ]
            )
        return pd.DataFrame([t.__dict__ for t in self.trades])

    # ------------------------------------------------------------------
    # Métricas
    # ------------------------------------------------------------------
    def summary(self) -> dict:
        df = self.to_frame()
        n = len(df)
        if n == 0:
            return {
                "sesiones": self.sessions_total,
                "sesiones_con_rango": self.sessions_with_range,
                "operaciones": 0,
            }
        wins = df[df["r_multiple"] > 0]
        losses = df[df["r_multiple"] <= 0]
        gross_win = wins["r_multiple"].sum()
        gross_loss = -losses["r_multiple"].sum()
        equity = df["r_multiple"].cumsum()
        drawdown = equity - equity.cummax()
        return {
            "sesiones": self.sessions_total,
            "sesiones_con_rango": self.sessions_with_range,
            "operaciones": n,
            "largos": int((df["side"] == "long").sum()),
            "cortos": int((df["side"] == "short").sum()),
            "tasa_acierto_pct": round(100.0 * len(wins) / n, 2),
            "resultado_total_R": round(float(df["r_multiple"].sum()), 3),
            "resultado_total_puntos": round(float(df["points"].sum()), 2),
            "media_R_por_operacion": round(float(df["r_multiple"].mean()), 3),
            "profit_factor": round(float(gross_win / gross_loss), 3)
            if gross_loss > 0
            else float("inf"),
            "max_drawdown_R": round(float(drawdown.min()), 3),
            "salidas_por_objetivo": int((df["exit_reason"] == "target").sum()),
            "salidas_por_stop": int((df["exit_reason"] == "stop").sum()),
            "salidas_por_tiempo": int((df["exit_reason"] == "time").sum()),
        }


def _simulate_trade(
    session: pd.DataFrame, signal: Signal, config: StrategyConfig
) -> Trade | None:
    """Simula una operación vela a vela hasta stop, objetivo o fin de ventana."""
    slip = config.slippage_points
    entry = signal.entry_price + slip if signal.side == "long" else signal.entry_price - slip
    risk = abs(entry - signal.stop_price)
    if risk <= 0:
        return None

    window_end = signal.entry_time.normalize() + pd.Timedelta(
        hours=config.session_open.hour, minutes=config.session_open.minute
    ) + pd.Timedelta(minutes=config.trading_window_minutes)

    bars = session[(session.index >= signal.entry_time) & (session.index < window_end)]
    if bars.empty:
        return None

    exit_time = bars.index[-1]
    exit_price = float(bars.iloc[-1]["close"])
    exit_reason = "time"

    for ts, bar in bars.iterrows():
        low, high = float(bar["low"]), float(bar["high"])
        if signal.side == "long":
            hit_stop = low <= signal.stop_price
            hit_target = high >= signal.target_price
        else:
            hit_stop = high >= signal.stop_price
            hit_target = low <= signal.target_price

        if hit_stop and hit_target:
            # Ambos niveles dentro de la misma vela: caso ambiguo.
            chosen = "stop" if config.conservative_same_bar else "target"
        elif hit_stop:
            chosen = "stop"
        elif hit_target:
            chosen = "target"
        else:
            continue

        exit_time = ts
        exit_reason = chosen
        exit_price = signal.stop_price if chosen == "stop" else signal.target_price
        break

    if signal.side == "long":
        points = (exit_price - slip) - entry
    else:
        points = entry - (exit_price + slip)
    points -= config.commission_points

    return Trade(
        date=signal.entry_time.normalize(),
        side=signal.side,
        entry_time=signal.entry_time,
        entry_price=entry,
        stop_price=signal.stop_price,
        target_price=signal.target_price,
        exit_time=exit_time,
        exit_price=exit_price,
        exit_reason=exit_reason,
        points=float(points),
        r_multiple=float(points / risk),
    )


def run_backtest(data: pd.DataFrame, config: StrategyConfig | None = None) -> BacktestResult:
    """Ejecuta el backtest sobre un DataFrame de velas de 1 minuto.

    El índice debe ser un DatetimeIndex con zona horaria (idealmente la de la
    sesión configurada); se agrupa por día natural y cada día se procesa como
    una sesión independiente.
    """
    config = config or StrategyConfig()
    config.validate()

    if data.index.tz is None:
        raise ValueError("El índice debe tener zona horaria (usa data.load_csv)")
    data = data.tz_convert(config.timezone) if hasattr(data, "tz_convert") else data
    data = data.sort_index()

    result = BacktestResult()
    for day, session in data.groupby(data.index.normalize()):
        result.sessions_total += 1
        signals = find_signals(session, config)
        if not signals:
            continue
        result.sessions_with_range += 1

        taken = 0
        last_exit: pd.Timestamp | None = None
        for sig in signals:
            if taken >= config.max_trades_per_session:
                break
            # No solapar operaciones: la siguiente señal debe ser posterior a
            # la salida de la operación anterior.
            if last_exit is not None and sig.entry_time <= last_exit:
                continue
            trade = _simulate_trade(session, sig, config)
            if trade is None:
                continue
            result.trades.append(trade)
            taken += 1
            last_exit = trade.exit_time

    return result

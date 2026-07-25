"""Tests del motor de backtesting con sesiones construidas a mano."""

from __future__ import annotations

import sys
from datetime import time
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from primera_vela.backtest import run_backtest
from primera_vela.config import StrategyConfig
from primera_vela.strategy import compute_opening_range, find_signals

TZ = "America/New_York"


def make_session(bars: list[tuple[str, float, float, float, float]], day: str = "2025-03-03") -> pd.DataFrame:
    """bars: lista de (HH:MM, open, high, low, close)."""
    idx, o, h, l, c = [], [], [], [], []
    for hhmm, op, hi, lo, cl in bars:
        idx.append(pd.Timestamp(f"{day} {hhmm}", tz=TZ))
        o.append(op)
        h.append(hi)
        l.append(lo)
        c.append(cl)
    return pd.DataFrame(
        {"open": o, "high": h, "low": l, "close": c, "volume": 1000.0},
        index=pd.DatetimeIndex(idx),
    )


def base_config(**overrides) -> StrategyConfig:
    defaults = dict(
        timezone=TZ,
        session_open=time(9, 30),
        opening_range_minutes=5,
        trading_window_minutes=90,
        risk_reward=1.0,
        max_breakout_fraction=None,
        swing_lookback=1,
        max_trades_per_session=1,
    )
    defaults.update(overrides)
    return StrategyConfig(**defaults)


def opening_five(base: float = 100.0) -> list[tuple[str, float, float, float, float]]:
    """Primeros 5 minutos: rango 95-105."""
    return [
        ("09:30", base, base + 5, base - 5, base + 2),
        ("09:31", base + 2, base + 4, base - 2, base + 1),
        ("09:32", base + 1, base + 3, base - 3, base),
        ("09:33", base, base + 2, base - 4, base - 1),
        ("09:34", base - 1, base + 1, base - 2, base),
    ]


def test_opening_range():
    session = make_session(opening_five())
    orange = compute_opening_range(session, base_config())
    assert orange is not None
    assert orange.high == 105.0
    assert orange.low == 95.0


def test_long_breakout_hits_target():
    bars = opening_five() + [
        ("09:35", 100, 104, 99, 103),    # dentro del rango: sin señal
        ("09:36", 103, 107, 102, 106),   # cierra sobre 105: señal larga
        ("09:37", 106, 108, 105.5, 107), # entrada en open=106
        ("09:38", 107, 117, 106, 115),   # objetivo alcanzado
    ]
    session = make_session(bars)
    result = run_backtest(session, base_config())
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.side == "long"
    assert t.entry_price == 106.0
    # swing low con lookback=1 antes de la entrada: pivote en 09:33 (low=96)
    assert t.stop_price == 96.0
    assert t.target_price == pytest.approx(116.0)
    assert t.exit_reason == "target"
    assert t.r_multiple == pytest.approx(1.0)


def test_short_breakout_hits_stop():
    bars = opening_five() + [
        ("09:35", 100, 101, 93, 94),     # cierra bajo 95: señal corta
        ("09:36", 94, 95, 93, 94),       # entrada en open=94
        ("09:37", 94, 106, 93.5, 102),   # el precio vuelve y toca el stop (105)
    ]
    session = make_session(bars)
    config = base_config()
    result = run_backtest(session, config)
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.side == "short"
    # Sin swing high previo utilizable por encima de la entrada cercana, el
    # stop es el máximo del rango (105).
    assert t.stop_price == 105.0
    assert t.exit_reason == "stop"
    assert t.r_multiple == pytest.approx(-1.0)


def test_exaggerated_breakout_filtered():
    bars = opening_five() + [
        # Rango = 10 puntos; cierre a 100.2 sobre el máximo -> exagerado.
        ("09:35", 100, 210, 99, 205.2),
        ("09:36", 205, 206, 204, 205),
    ]
    session = make_session(bars)
    config = base_config(max_breakout_fraction=0.5)
    assert find_signals(session, config) == []
    # Sin filtro sí hay señal.
    config_sin_filtro = base_config(max_breakout_fraction=None)
    assert len(find_signals(session, config_sin_filtro)) == 1


def test_time_exit_at_window_end():
    bars = opening_five() + [
        ("09:36", 103, 107, 102, 106),  # ruptura larga
        ("09:37", 106, 107, 105, 106),  # entrada; después lateral
    ]
    # Velas laterales hasta pasadas las 11:00 (fin de ventana de 90 min).
    t = pd.Timestamp("2025-03-03 09:38", tz=TZ)
    while t <= pd.Timestamp("2025-03-03 11:05", tz=TZ):
        bars.append((t.strftime("%H:%M"), 106, 106.5, 105.5, 106))
        t += pd.Timedelta(minutes=1)
    session = make_session(bars)
    result = run_backtest(session, base_config())
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "time"
    assert trade.exit_time == pd.Timestamp("2025-03-03 10:59", tz=TZ)


def test_no_trade_after_window():
    # La ruptura ocurre pasados los 90 minutos: no debe operarse.
    bars = opening_five() + [
        ("09:36", 100, 104, 99, 103),
        ("11:01", 103, 120, 102, 119),
        ("11:02", 119, 121, 118, 120),
    ]
    session = make_session(bars)
    assert find_signals(session, base_config()) == []


def test_max_one_trade_per_session():
    bars = opening_five() + [
        ("09:36", 103, 107, 102, 106),   # señal larga 1
        ("09:37", 106, 108, 105, 107),   # entrada 1
        ("09:38", 107, 107.5, 90, 91),   # stop
        ("09:39", 91, 93, 90, 92),       # cierra bajo 95: sería señal corta
        ("09:40", 92, 93, 91, 92),
    ]
    session = make_session(bars)
    result = run_backtest(session, base_config(max_trades_per_session=1))
    assert len(result.trades) == 1
    result2 = run_backtest(session, base_config(max_trades_per_session=2))
    assert len(result2.trades) == 2


def test_conservative_same_bar():
    bars = opening_five() + [
        ("09:35", 100, 106, 99, 106),     # señal larga (cierra en el máximo de la vela)
        ("09:36", 106, 120, 90, 100),     # vela enorme: toca stop y objetivo
    ]
    session = make_session(bars)
    res_cons = run_backtest(session, base_config(conservative_same_bar=True))
    assert res_cons.trades[0].exit_reason == "stop"
    res_opt = run_backtest(session, base_config(conservative_same_bar=False))
    assert res_opt.trades[0].exit_reason == "target"


def test_synthetic_generator_runs():
    from primera_vela.data import generate_synthetic

    df = generate_synthetic(sessions=5, seed=1)
    result = run_backtest(df, base_config(max_breakout_fraction=0.5, swing_lookback=2))
    assert result.sessions_total == 5
    # Con 5 sesiones volátiles debería haber al menos una operación.
    assert len(result.trades) >= 1

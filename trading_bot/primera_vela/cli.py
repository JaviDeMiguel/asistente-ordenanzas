"""CLI del bot de backtesting "Primera Vela".

Uso típico:

    # Con datos propios en CSV (velas de 1 minuto)
    python -m primera_vela.cli backtest --csv datos_nq_1m.csv

    # Descargando de Yahoo Finance (últimos ~25 días de 1m)
    python -m primera_vela.cli descargar --simbolo NQ=F --dias 25 --salida nq.csv
    python -m primera_vela.cli backtest --csv nq.csv

    # Sin conexión, con datos sintéticos para probar el motor
    python -m primera_vela.cli backtest --sintetico 60
"""

from __future__ import annotations

import argparse
import json
import sys

from . import data as data_mod
from .backtest import run_backtest
from .config import StrategyConfig


def _add_strategy_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--zona-horaria", default="America/New_York")
    p.add_argument("--apertura", default="09:30", help="Hora de apertura HH:MM")
    p.add_argument("--rango-min", type=int, default=5, help="Minutos de la primera vela")
    p.add_argument("--ventana-min", type=int, default=90, help="Ventana operativa en minutos")
    p.add_argument("--risk-reward", type=float, default=1.0)
    p.add_argument(
        "--max-breakout",
        type=float,
        default=0.5,
        help="Fracción del rango que puede alejarse el cierre de ruptura (filtro de breakout exagerado); -1 desactiva",
    )
    p.add_argument("--swing-lookback", type=int, default=2)
    p.add_argument("--max-operaciones", type=int, default=1, help="Operaciones máximas por sesión")
    p.add_argument("--solo-largos", action="store_true")
    p.add_argument("--solo-cortos", action="store_true")
    p.add_argument("--comision", type=float, default=0.0, help="Coste ida y vuelta en puntos")
    p.add_argument("--slippage", type=float, default=0.0, help="Deslizamiento por fill en puntos")
    p.add_argument(
        "--mismo-bar-optimista",
        action="store_true",
        help="Si stop y objetivo caen en la misma vela, asumir objetivo en vez de stop",
    )


def _config_from_args(args: argparse.Namespace) -> StrategyConfig:
    from datetime import time

    hh, mm = args.apertura.split(":")
    return StrategyConfig(
        timezone=args.zona_horaria,
        session_open=time(int(hh), int(mm)),
        opening_range_minutes=args.rango_min,
        trading_window_minutes=args.ventana_min,
        risk_reward=args.risk_reward,
        max_breakout_fraction=None if args.max_breakout < 0 else args.max_breakout,
        swing_lookback=args.swing_lookback,
        max_trades_per_session=args.max_operaciones,
        allow_longs=not args.solo_cortos,
        allow_shorts=not args.solo_largos,
        commission_points=args.comision,
        slippage_points=args.slippage,
        conservative_same_bar=not args.mismo_bar_optimista,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="primera_vela",
        description="Backtesting de la estrategia 'Primera Vela' (ORB 5m/1m)",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    p_dl = sub.add_parser("descargar", help="Descarga velas de 1m con yfinance")
    p_dl.add_argument("--simbolo", default="NQ=F")
    p_dl.add_argument("--dias", type=int, default=25)
    p_dl.add_argument("--salida", required=True)
    p_dl.add_argument("--zona-horaria", default="America/New_York")

    p_bt = sub.add_parser("backtest", help="Ejecuta el backtest")
    src = p_bt.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="CSV con velas de 1 minuto")
    src.add_argument(
        "--sintetico",
        type=int,
        metavar="SESIONES",
        help="Genera N sesiones sintéticas (sin conexión)",
    )
    p_bt.add_argument("--semilla", type=int, default=7, help="Semilla del generador sintético")
    p_bt.add_argument("--trades-csv", help="Ruta donde guardar el registro de operaciones")
    p_bt.add_argument("--json", action="store_true", help="Imprime el resumen en JSON")
    _add_strategy_args(p_bt)

    args = parser.parse_args(argv)

    if args.comando == "descargar":
        df = data_mod.download_yfinance(args.simbolo, args.dias, args.zona_horaria)
        df.to_csv(args.salida, index_label="timestamp")
        print(f"Guardadas {len(df)} velas de 1m en {args.salida}")
        return 0

    config = _config_from_args(args)
    if args.csv:
        df = data_mod.load_csv(args.csv, config.timezone)
    else:
        df = data_mod.generate_synthetic(args.sintetico, seed=args.semilla, timezone=config.timezone)

    result = run_backtest(df, config)
    summary = result.summary()

    if args.trades_csv:
        result.to_frame().to_csv(args.trades_csv, index=False)
        print(f"Operaciones guardadas en {args.trades_csv}", file=sys.stderr)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    else:
        print("\n=== Estrategia 'Primera Vela' — resumen del backtest ===")
        for k, v in summary.items():
            print(f"  {k:>24}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

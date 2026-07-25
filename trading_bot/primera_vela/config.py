"""Configuración de la estrategia "Primera Vela".

Todos los parámetros tienen como valor por defecto las reglas tal y como se
describen en el vídeo:

- Rango de apertura: primera vela de 5 minutos de la sesión de Nueva York
  (09:30-09:35 hora de Nueva York).
- Entrada: cierre de una vela de 1 minuto por encima del máximo (largo) o por
  debajo del mínimo (corto) del rango.
- Stop loss: último mínimo/máximo relevante (swing) en M1.
- Objetivo: Risk/Reward 1:1.
- Filtro: no entrar si el breakout es "exagerado" (el cierre queda demasiado
  lejos del nivel roto).
- Ventana operativa: los primeros 90 minutos de la sesión.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time


@dataclass
class StrategyConfig:
    # Zona horaria de la sesión (el vídeo usa la apertura de Nueva York).
    timezone: str = "America/New_York"
    session_open: time = time(9, 30)

    # Minutos que dura la "primera vela" que define el rango.
    opening_range_minutes: int = 5

    # Ventana operativa desde la apertura (en minutos). Pasado este tiempo no
    # se abren operaciones nuevas y las abiertas se cierran a mercado.
    trading_window_minutes: int = 90

    # Risk/Reward del objetivo respecto al riesgo asumido (1.0 = 1:1).
    risk_reward: float = 1.0

    # Filtro de breakout exagerado: se descarta la señal si el cierre de la
    # vela de ruptura queda a más de esta fracción del tamaño del rango por
    # encima/debajo del nivel roto. None desactiva el filtro.
    max_breakout_fraction: float | None = 0.5

    # Número de velas a cada lado para detectar el swing (pivote) de M1 usado
    # como stop loss. Si no hay pivote disponible se usa el extremo opuesto
    # del rango de apertura.
    swing_lookback: int = 2

    # Máximo de operaciones por sesión (el vídeo opera la primera señal).
    max_trades_per_session: int = 1

    # Permitir operar en ambas direcciones.
    allow_longs: bool = True
    allow_shorts: bool = True

    # Fricción: coste por operación ida y vuelta (en puntos del instrumento)
    # y deslizamiento aplicado a cada fill (en puntos).
    commission_points: float = 0.0
    slippage_points: float = 0.0

    # Si el stop y el objetivo caen dentro de la misma vela de 1 minuto no se
    # puede saber cuál se tocó primero; True asume el peor caso (stop).
    conservative_same_bar: bool = True

    def validate(self) -> None:
        if self.opening_range_minutes <= 0:
            raise ValueError("opening_range_minutes debe ser positivo")
        if self.trading_window_minutes <= self.opening_range_minutes:
            raise ValueError(
                "trading_window_minutes debe ser mayor que opening_range_minutes"
            )
        if self.risk_reward <= 0:
            raise ValueError("risk_reward debe ser positivo")
        if self.max_breakout_fraction is not None and self.max_breakout_fraction < 0:
            raise ValueError("max_breakout_fraction no puede ser negativo")
        if self.swing_lookback < 1:
            raise ValueError("swing_lookback debe ser >= 1")

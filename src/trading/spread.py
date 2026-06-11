from __future__ import annotations

from typing import Any


def compute_mt5_spread(mt5_tick: dict[str, Any] | None) -> float | None:
    if not mt5_tick:
        return None
    bid = mt5_tick.get("bid")
    ask = mt5_tick.get("ask")
    point = mt5_tick.get("point")
    if bid is None or ask is None or point is None:
        return None
    point_value = float(point)
    if point_value <= 0:
        return None
    return round((float(ask) - float(bid)) / point_value, 2)


def compute_binance_spread(binance_tick: dict[str, Any] | None) -> float | None:
    if not binance_tick:
        return None
    bid = binance_tick.get("bid")
    ask = binance_tick.get("ask")
    if bid is None or ask is None:
        return None
    return round(float(ask) - float(bid), 2)

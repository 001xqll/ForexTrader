from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TickSnapshot:
    """Egy pillanatnyi MT5 + Binance ár és különbség."""

    seq: int
    ts_mono: float
    mt5_symbol: str
    binance_symbol: str
    mt5_bid: float | None
    binance_price: float | None
    diff: float | None
    mt5_spread: float | None
    binance_spread: float | None
    fetch_duration_ms: float
    mt5_tick: dict[str, Any] | None
    binance_tick: dict[str, Any] | None
    source: str = "rest_poll"

    @property
    def is_complete(self) -> bool:
        return self.mt5_bid is not None and self.binance_price is not None and self.diff is not None

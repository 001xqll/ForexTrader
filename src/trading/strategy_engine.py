from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from src.trading.order_executor import OrderExecutor
from src.trading.tick_snapshot import TickSnapshot

LogFn = Callable[[str], None]
TradingAllowedFn = Callable[[], bool]


class StrategyEngine:
    """Tick alapú döntés — a grafikon nélkül fut. 1. fázis: mérés + dry-run váz."""

    STATS_EVERY_TICKS = 200

    def __init__(
        self,
        order_executor: OrderExecutor,
        is_trading_allowed: TradingAllowedFn,
        log: LogFn,
    ) -> None:
        self._order_executor = order_executor
        self._is_trading_allowed = is_trading_allowed
        self._log = log
        self._tick_count = 0
        self._complete_tick_count = 0
        self._fetch_total_ms = 0.0
        self._eval_total_ms = 0.0
        self._last_complete: TickSnapshot | None = None
        self._signal_cooldown_sec = 5.0
        self._last_signal_mono = 0.0
        self._ws_tick_count = 0
        self._poll_tick_count = 0

    def on_tick(self, snapshot: TickSnapshot) -> None:
        eval_started = time.perf_counter()

        self._tick_count += 1
        self._fetch_total_ms += snapshot.fetch_duration_ms

        if not snapshot.is_complete:
            return

        self._complete_tick_count += 1
        self._last_complete = snapshot
        if snapshot.source == "binance_ws":
            self._ws_tick_count += 1
        else:
            self._poll_tick_count += 1
        self._evaluate(snapshot)

        eval_ms = (time.perf_counter() - eval_started) * 1000
        self._eval_total_ms += eval_ms

        if self._complete_tick_count % self.STATS_EVERY_TICKS == 0:
            self._log_stats()

    def _evaluate(self, snapshot: TickSnapshot) -> None:
        if not self._is_trading_allowed():
            return

        # Konkrét stratégia később — most csak a pipeline mérése.
        # Példa jel: diff abszolút értéke > 99999 soha nem teljesül élesben.
        signal = self._check_placeholder_signal(snapshot)
        if signal is None:
            return

        now = time.perf_counter()
        if now - self._last_signal_mono < self._signal_cooldown_sec:
            return
        self._last_signal_mono = now

        signal_mono = time.perf_counter()
        signal_to_order_ms = (signal_mono - snapshot.ts_mono) * 1000
        self._log(
            f"[Jel] seq={snapshot.seq} diff={snapshot.diff:+.2f} "
            f"tick→jel={signal_to_order_ms:.2f} ms (fetch={snapshot.fetch_duration_ms:.1f} ms)"
        )

        self._order_executor.open_spread_position(
            snapshot,
            direction=signal["direction"],
            volume=signal.get("volume", 0.0),
        )

    def _check_placeholder_signal(self, snapshot: TickSnapshot) -> dict[str, Any] | None:
        return None

    def _log_stats(self) -> None:
        if self._complete_tick_count == 0:
            return
        avg_fetch = self._fetch_total_ms / self._tick_count
        avg_eval = self._eval_total_ms / self._complete_tick_count
        diff_text = f"{self._last_complete.diff:+.2f}" if self._last_complete else "—"
        self._log(
            f"[Tick motor] {self._complete_tick_count} teljes tick | "
            f"átlag fetch={avg_fetch:.1f} ms | átlag stratégia={avg_eval:.2f} ms | "
            f"ws={self._ws_tick_count} poll={self._poll_tick_count} | "
            f"utolsó diff={diff_text}"
        )

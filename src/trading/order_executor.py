from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from src.brokers.binance_client import BinanceFuturesClient
from src.brokers.mt5_client import MT5Client
from src.trading.tick_snapshot import TickSnapshot

LogFn = Callable[[str], None]


@dataclass
class OrderLegResult:
    venue: str
    success: bool
    message: str
    duration_ms: float


@dataclass
class SpreadOrderResult:
    signal_seq: int
    dry_run: bool
    started_mono: float
    total_duration_ms: float
    mt5: OrderLegResult
    binance: OrderLegResult


class OrderExecutor:
    """MT5 + Binance lábak párhuzamos végrehajtása (1. fázis: dry-run váz)."""

    def __init__(
        self,
        mt5: MT5Client,
        binance: BinanceFuturesClient,
        log: LogFn,
        *,
        dry_run: bool = True,
    ) -> None:
        self._mt5 = mt5
        self._binance = binance
        self._log = log
        self._dry_run = dry_run
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="order-exec")

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def open_spread_position(
        self,
        snapshot: TickSnapshot,
        *,
        direction: str,
        volume: float = 0.0,
    ) -> SpreadOrderResult:
        started = time.perf_counter()
        started_mono = time.perf_counter()

        mt5_future = self._pool.submit(
            self._place_mt5_leg,
            snapshot,
            direction,
            volume,
            started,
        )
        binance_future = self._pool.submit(
            self._place_binance_leg,
            snapshot,
            direction,
            volume,
            started,
        )
        mt5_result = mt5_future.result()
        binance_result = binance_future.result()

        total_ms = (time.perf_counter() - started_mono) * 1000
        result = SpreadOrderResult(
            signal_seq=snapshot.seq,
            dry_run=self._dry_run,
            started_mono=started_mono,
            total_duration_ms=total_ms,
            mt5=mt5_result,
            binance=binance_result,
        )
        self._log_order_result(result, snapshot)
        return result

    def _place_mt5_leg(
        self,
        snapshot: TickSnapshot,
        direction: str,
        volume: float,
        signal_started: float,
    ) -> OrderLegResult:
        leg_started = time.perf_counter()
        queue_ms = (leg_started - signal_started) * 1000

        if self._dry_run:
            duration_ms = (time.perf_counter() - leg_started) * 1000
            return OrderLegResult(
                venue="MT5",
                success=True,
                message=f"dry-run {direction} {snapshot.mt5_symbol} vol={volume} (queue {queue_ms:.1f} ms)",
                duration_ms=duration_ms,
            )

        # Éles order — későbbi fázis
        duration_ms = (time.perf_counter() - leg_started) * 1000
        return OrderLegResult(
            venue="MT5",
            success=False,
            message="MT5 order még nincs implementálva.",
            duration_ms=duration_ms,
        )

    def _place_binance_leg(
        self,
        snapshot: TickSnapshot,
        direction: str,
        volume: float,
        signal_started: float,
    ) -> OrderLegResult:
        leg_started = time.perf_counter()
        queue_ms = (leg_started - signal_started) * 1000

        if self._dry_run:
            duration_ms = (time.perf_counter() - leg_started) * 1000
            return OrderLegResult(
                venue="Binance",
                success=True,
                message=(
                    f"dry-run {direction} {snapshot.binance_symbol} "
                    f"vol={volume} (queue {queue_ms:.1f} ms)"
                ),
                duration_ms=duration_ms,
            )

        duration_ms = (time.perf_counter() - leg_started) * 1000
        return OrderLegResult(
            venue="Binance",
            success=False,
            message="Binance order még nincs implementálva.",
            duration_ms=duration_ms,
        )

    def _log_order_result(self, result: SpreadOrderResult, snapshot: TickSnapshot) -> None:
        mode = "DRY-RUN" if result.dry_run else "ÉLES"
        self._log(
            f"[Order {mode}] seq={result.signal_seq} diff={snapshot.diff:+.2f} "
            f"összes={result.total_duration_ms:.1f} ms | "
            f"MT5={result.mt5.duration_ms:.1f} ms | "
            f"Binance={result.binance.duration_ms:.1f} ms"
        )

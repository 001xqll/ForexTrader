from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TypeVar
from src.brokers.binance_client import BinanceFuturesClient
from src.brokers.mt5_client import MT5Client
from src.logger.app_logger import log_warning
from src.trading.tick_snapshot import TickSnapshot

LogFn = Callable[[str], None]
MainThreadScheduler = Callable[[Callable[[], None]], None]
Mt5PauseCallback = Callable[[bool], None]
T = TypeVar("T")


@dataclass
class OrderLegResult:
    venue: str
    success: bool
    message: str
    duration_ms: float
    fill_price: float | None = None


@dataclass
class SpreadOrderResult:
    signal_seq: int
    dry_run: bool
    direction: str
    level: float
    started_mono: float
    total_duration_ms: float
    mt5: OrderLegResult
    binance: OrderLegResult


@dataclass
class CloseHedgeResult:
    signal_seq: int
    dry_run: bool
    direction: str
    started_mono: float
    total_duration_ms: float
    mt5: OrderLegResult
    binance: OrderLegResult

    @property
    def success(self) -> bool:
        return self.mt5.success and self.binance.success


class OrderExecutor:
    """MT5 + Binance hedge nyitás — MT5 a főszálon (thread-safe)."""

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
        self._main_scheduler: MainThreadScheduler | None = None
        self._mt5_pause_callback: Mt5PauseCallback | None = None

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @dry_run.setter
    def dry_run(self, value: bool) -> None:
        self._dry_run = value

    def set_main_thread_scheduler(self, scheduler: MainThreadScheduler | None) -> None:
        self._main_scheduler = scheduler

    def set_mt5_pause_callback(self, callback: Mt5PauseCallback | None) -> None:
        self._mt5_pause_callback = callback

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def open_level_hedge(
        self,
        snapshot: TickSnapshot,
        *,
        direction: str,
        level: float,
        lot_mt5: float,
        binance_qty: float,
    ) -> SpreadOrderResult:
        if not self._dry_run and self._mt5_pause_callback is not None:
            self._mt5_pause_callback(True)
        try:
            return self._open_level_hedge_impl(
                snapshot, direction, level, lot_mt5, binance_qty
            )
        finally:
            if not self._dry_run and self._mt5_pause_callback is not None:
                self._mt5_pause_callback(False)

    def close_hedge_pair(self, snapshot: TickSnapshot, *, direction: str) -> CloseHedgeResult:
        if not self._dry_run and self._mt5_pause_callback is not None:
            self._mt5_pause_callback(True)
        try:
            return self._close_hedge_pair_impl(snapshot, direction)
        finally:
            if not self._dry_run and self._mt5_pause_callback is not None:
                self._mt5_pause_callback(False)

    def _run_on_main_thread(self, fn: Callable[[], T]) -> T | None:
        if self._main_scheduler is None:
            return fn()

        holder: list[T] = []
        done = threading.Event()

        def run_on_main() -> None:
            try:
                holder.append(fn())
            finally:
                done.set()

        self._main_scheduler(run_on_main)
        if not done.wait(timeout=60.0):
            return None
        return holder[0] if holder else None

    def _open_level_hedge_impl(
        self,
        snapshot: TickSnapshot,
        direction: str,
        level: float,
        lot_mt5: float,
        binance_qty: float,
    ) -> SpreadOrderResult:
        started_mono = time.perf_counter()

        if direction == "pos":
            mt5_side, binance_side = "SELL", "BUY"
        else:
            mt5_side, binance_side = "BUY", "SELL"

        if self._dry_run:
            mt5_result = OrderLegResult("MT5", True, "dry-run", 0.0)
            binance_result = OrderLegResult("Binance", True, "dry-run", 0.0)
        else:
            mt5_result, binance_result = self._execute_live_hedge(
                snapshot, mt5_side, binance_side, lot_mt5, binance_qty, started_mono
            )

        total_ms = (time.perf_counter() - started_mono) * 1000
        return SpreadOrderResult(
            signal_seq=snapshot.seq,
            dry_run=self._dry_run,
            direction=direction,
            level=level,
            started_mono=started_mono,
            total_duration_ms=total_ms,
            mt5=mt5_result,
            binance=binance_result,
        )

    def _execute_live_hedge(
        self,
        snapshot: TickSnapshot,
        mt5_side: str,
        binance_side: str,
        lot_mt5: float,
        binance_qty: float,
        started_mono: float,
    ) -> tuple[OrderLegResult, OrderLegResult]:
        binance_timing: dict[str, float | dict | None] = {}

        def run_binance_leg() -> tuple[bool, str, float | None]:
            leg_start = time.perf_counter()
            order: dict | None = None
            api_ms = 0.0
            try:
                ok, msg, fill_price, order, api_ms = self._binance.create_market_order(
                    snapshot.binance_symbol,
                    binance_side,
                    binance_qty,
                )
            except Exception as exc:  # noqa: BLE001
                ok, msg, fill_price, order = False, str(exc), None, None
            done_mono = time.perf_counter()
            elapsed_from_start = (done_mono - started_mono) * 1000
            leg_duration = (done_mono - leg_start) * 1000
            binance_timing["elapsed_from_start"] = elapsed_from_start
            binance_timing["leg_duration"] = leg_duration
            binance_timing["fill_price"] = fill_price
            binance_timing["order"] = order
            detail = self._format_leg_detail(
                binance_side,
                snapshot.binance_symbol,
                f"qty={binance_qty}",
            )
            self._log_leg_opened(
                "Binance",
                elapsed_from_start_ms=elapsed_from_start,
                leg_duration_ms=leg_duration,
                success=ok,
                detail=detail,
                dry_run=False,
                api_ms=api_ms,
            )
            return ok, msg, fill_price

        bin_future = self._pool.submit(run_binance_leg)

        mt5_leg_start = time.perf_counter()
        mt5_result_tuple = self._run_on_main_thread(
            lambda: self._mt5.open_market_order(snapshot.mt5_symbol, mt5_side, lot_mt5)
        )
        mt5_ticket: int | None = None
        if mt5_result_tuple is None:
            mt5_ok, mt5_msg, mt5_fill = False, "Időtúllépés a főszálon.", None
        else:
            mt5_ok, mt5_msg, mt5_fill, mt5_ticket = mt5_result_tuple
        mt5_done_mono = time.perf_counter()
        mt5_elapsed_from_start = (mt5_done_mono - started_mono) * 1000
        mt5_leg_duration = (mt5_done_mono - mt5_leg_start) * 1000
        mt5_detail = self._format_leg_detail(
            mt5_side, snapshot.mt5_symbol, f"vol={lot_mt5}"
        )
        self._log_leg_opened(
            "MT5",
            elapsed_from_start_ms=mt5_elapsed_from_start,
            leg_duration_ms=mt5_leg_duration,
            success=mt5_ok,
            detail=mt5_detail,
            dry_run=False,
        )

        try:
            bin_ok, bin_msg, bin_fill = bin_future.result(timeout=30)
        except Exception as exc:  # noqa: BLE001
            bin_ok, bin_msg, bin_fill = False, str(exc), None
        bin_elapsed_from_start = binance_timing.get(
            "elapsed_from_start",
            (time.perf_counter() - started_mono) * 1000,
        )
        bin_leg_duration = binance_timing.get("leg_duration", 0.0)
        if bin_fill is None:
            stored_fill = binance_timing.get("fill_price")
            if isinstance(stored_fill, (int, float)):
                bin_fill = float(stored_fill)

        self._log_leg_gap(mt5_elapsed_from_start, bin_elapsed_from_start, dry_run=False)

        if mt5_ok and bin_ok:
            binance_order = binance_timing.get("order")
            self._schedule_fill_prices_log(
                mt5_fill=mt5_fill,
                binance_symbol=snapshot.binance_symbol,
                binance_order=binance_order if isinstance(binance_order, dict) else None,
                binance_fill=bin_fill,
            )

        if mt5_ok and not bin_ok:
            self._log(
                f"Binance nyitás sikertelen — MT5 visszagörgetés ({lot_mt5:g} lot)."
            )
            ticket = mt5_ticket

            def rollback_mt5() -> tuple[bool, str]:
                return self._mt5.rollback_open_leg(
                    snapshot.mt5_symbol,
                    volume=lot_mt5,
                    side=mt5_side,
                    position_ticket=ticket,
                )

            rollback_ok, rollback_msg = self._run_on_main_thread(rollback_mt5) or (
                False,
                "Időtúllépés a főszálon.",
            )
            if not rollback_ok:
                log_warning(f"MT5 visszagörgetés sikertelen: {rollback_msg}")
            else:
                self._log(rollback_msg)
        elif bin_ok and not mt5_ok:
            self._log(
                f"MT5 nyitás sikertelen — Binance visszagörgetés (qty={binance_qty:g})."
            )
            rollback_ok, rollback_msg = self._binance.rollback_open_leg(
                snapshot.binance_symbol,
                quantity=binance_qty,
                opened_side=binance_side,
            )
            if not rollback_ok:
                log_warning(f"Binance visszagörgetés sikertelen: {rollback_msg}")
            else:
                self._log(rollback_msg)

        return (
            OrderLegResult("MT5", mt5_ok, mt5_detail, mt5_leg_duration, mt5_fill),
            OrderLegResult("Binance", bin_ok, bin_msg, bin_leg_duration, bin_fill),
        )

    def _close_hedge_pair_impl(self, snapshot: TickSnapshot, direction: str) -> CloseHedgeResult:
        started_mono = time.perf_counter()

        if self._dry_run:
            mt5_result = OrderLegResult("MT5", True, "dry-run", 0.0)
            binance_result = OrderLegResult("Binance", True, "dry-run", 0.0)
        else:
            mt5_result, binance_result = self._execute_live_close(snapshot, started_mono)

        total_ms = (time.perf_counter() - started_mono) * 1000
        return CloseHedgeResult(
            signal_seq=snapshot.seq,
            dry_run=self._dry_run,
            direction=direction,
            started_mono=started_mono,
            total_duration_ms=total_ms,
            mt5=mt5_result,
            binance=binance_result,
        )

    def _execute_live_close(
        self,
        snapshot: TickSnapshot,
        started_mono: float,
    ) -> tuple[OrderLegResult, OrderLegResult]:
        binance_timing: dict[str, float] = {}

        def run_binance_close() -> tuple[bool, str]:
            leg_start = time.perf_counter()
            try:
                ok, msg = self._binance.close_all_positions(snapshot.binance_symbol)
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, str(exc)
            done_mono = time.perf_counter()
            elapsed_from_start = (done_mono - started_mono) * 1000
            leg_duration = (done_mono - leg_start) * 1000
            binance_timing["elapsed_from_start"] = elapsed_from_start
            binance_timing["leg_duration"] = leg_duration
            detail = f"zárás {snapshot.binance_symbol}"
            self._log_leg_closed(
                "Binance",
                elapsed_from_start_ms=elapsed_from_start,
                leg_duration_ms=leg_duration,
                success=ok,
                detail=detail if ok else msg,
                dry_run=False,
            )
            return ok, msg

        bin_future = self._pool.submit(run_binance_close)

        mt5_leg_start = time.perf_counter()
        mt5_result_tuple = self._run_on_main_thread(
            lambda: self._mt5.close_all_positions(snapshot.mt5_symbol)
        )
        if mt5_result_tuple is None:
            mt5_ok, mt5_msg = False, "Időtúllépés a főszálon."
        else:
            mt5_ok, mt5_msg = mt5_result_tuple
        mt5_done_mono = time.perf_counter()
        mt5_elapsed_from_start = (mt5_done_mono - started_mono) * 1000
        mt5_leg_duration = (mt5_done_mono - mt5_leg_start) * 1000
        mt5_detail = f"zárás {snapshot.mt5_symbol}"
        self._log_leg_closed(
            "MT5",
            elapsed_from_start_ms=mt5_elapsed_from_start,
            leg_duration_ms=mt5_leg_duration,
            success=mt5_ok,
            detail=mt5_detail if mt5_ok else mt5_msg,
            dry_run=False,
        )

        try:
            bin_ok, bin_msg = bin_future.result(timeout=30)
        except Exception as exc:  # noqa: BLE001
            bin_ok, bin_msg = False, str(exc)
        bin_elapsed_from_start = binance_timing.get(
            "elapsed_from_start",
            (time.perf_counter() - started_mono) * 1000,
        )
        bin_leg_duration = binance_timing.get("leg_duration", 0.0)

        self._log_leg_gap(mt5_elapsed_from_start, bin_elapsed_from_start, dry_run=False, action="Zárás")

        if mt5_ok and not bin_ok:
            self._log("Binance zárás sikertelen — újrapróbálás.")
            retry = self._binance.close_all_positions(snapshot.binance_symbol)
            bin_ok = retry[0]
            bin_msg = retry[1]
        elif bin_ok and not mt5_ok:
            self._log("MT5 zárás sikertelen — újrapróbálás.")
            retry = self._run_on_main_thread(
                lambda: self._mt5.close_all_positions(snapshot.mt5_symbol)
            )
            if retry is not None:
                mt5_ok, mt5_msg = retry

        if not (mt5_ok and bin_ok):
            self._log("Hedge zárás hiányos — következő ticknél újra próbálkozik.")

        return (
            OrderLegResult("MT5", mt5_ok, mt5_msg, mt5_leg_duration),
            OrderLegResult("Binance", bin_ok, bin_msg, bin_leg_duration),
        )

    def _failed_result(
        self,
        snapshot: TickSnapshot,
        direction: str,
        level: float,
        message: str,
    ) -> SpreadOrderResult:
        leg = OrderLegResult("—", False, message, 0.0)
        return SpreadOrderResult(
            signal_seq=snapshot.seq,
            dry_run=self._dry_run,
            direction=direction,
            level=level,
            started_mono=time.perf_counter(),
            total_duration_ms=0.0,
            mt5=leg,
            binance=leg,
        )

    @staticmethod
    def _format_price(price: float | None) -> str:
        if price is None:
            return "—"
        return f"{price:.2f}"

    @staticmethod
    def _reference_price(snapshot: TickSnapshot, venue: str, side: str) -> float | None:
        side_upper = side.upper()
        if venue == "MT5":
            tick = snapshot.mt5_tick or {}
            if side_upper == "BUY":
                ask = tick.get("ask")
                return float(ask) if ask is not None else None
            bid = tick.get("bid")
            if bid is not None:
                return float(bid)
            return float(snapshot.mt5_bid) if snapshot.mt5_bid is not None else None
        if venue == "Binance":
            return float(snapshot.binance_price) if snapshot.binance_price is not None else None
        return None

    @staticmethod
    def _format_leg_detail(side: str, symbol: str, size_part: str) -> str:
        return f"{side} {symbol} {size_part}"

    def _schedule_fill_prices_log(
        self,
        *,
        mt5_fill: float | None,
        binance_symbol: str,
        binance_order: dict | None,
        binance_fill: float | None,
    ) -> None:
        def task() -> None:
            fill = binance_fill
            if fill is None and binance_order is not None:
                fill = self._binance.poll_fill_price(binance_symbol, binance_order)
            self._log(
                f"[Fill ár] MT5 @ {self._format_price(mt5_fill)} | "
                f"Binance @ {self._format_price(fill)}"
            )

        self._pool.submit(task)

    def _log_leg_opened(
        self,
        venue: str,
        *,
        elapsed_from_start_ms: float,
        leg_duration_ms: float,
        success: bool,
        detail: str,
        dry_run: bool,
        api_ms: float | None = None,
    ) -> None:
        mode = "DRY-RUN" if dry_run else "ÉLES"
        status = "OK" if success else "HIBA"
        if api_ms is not None:
            timing = f"API: {api_ms:.1f} ms"
        else:
            timing = f"{leg_duration_ms:.1f} ms order"
        self._log(
            f"[Order {mode}] {venue} pozíció nyitva: +{elapsed_from_start_ms:.1f} ms a jeltől "
            f"({timing}) — {detail} [{status}]"
        )

    def _log_leg_closed(
        self,
        venue: str,
        *,
        elapsed_from_start_ms: float,
        leg_duration_ms: float,
        success: bool,
        detail: str,
        dry_run: bool,
    ) -> None:
        mode = "DRY-RUN" if dry_run else "ÉLES"
        status = "OK" if success else "HIBA"
        note = " (szimulált)" if dry_run else ""
        self._log(
            f"[Zárás {mode}] {venue} pozíció zárva: +{elapsed_from_start_ms:.1f} ms a jeltől "
            f"({leg_duration_ms:.1f} ms order) — {detail}{note} [{status}]"
        )

    def _log_leg_gap(
        self,
        mt5_elapsed_ms: float,
        binance_elapsed_ms: float,
        *,
        dry_run: bool,
        action: str = "Order",
    ) -> None:
        mode = "DRY-RUN" if dry_run else "ÉLES"
        gap = abs(mt5_elapsed_ms - binance_elapsed_ms)
        if gap < 0.05:
            first = "egyszerre"
        elif mt5_elapsed_ms < binance_elapsed_ms:
            first = "MT5"
        else:
            first = "Binance"
        prefix = "Zárás" if action == "Zárás" else "Order"
        if first == "egyszerre":
            self._log(f"[{prefix} {mode}] Lábak közti eltérés: {gap:.1f} ms (egyszerre)")
        else:
            self._log(
                f"[{prefix} {mode}] Lábak közti eltérés: {gap:.1f} ms ({first} előbb végzett)"
            )

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from src.brokers.binance_client import BinanceFuturesClient
from src.brokers.binance_futures_stream import BinanceFuturesPriceStream
from src.brokers.mt5_client import MT5Client
from src.trading.tick_snapshot import TickSnapshot

TickCallback = Callable[[TickSnapshot], None]


class TickEngine:
    """MT5 poll + Binance websocket (vagy REST fallback). A kereskedés nem vár a grafikonra."""

    WS_MIN_EMIT_MS = 100

    def __init__(
        self,
        mt5: MT5Client,
        binance: BinanceFuturesClient,
        interval_ms_getter: Callable[[], int],
        use_websocket_getter: Callable[[], bool] | None = None,
    ) -> None:
        self._mt5 = mt5
        self._binance = binance
        self._interval_ms_getter = interval_ms_getter
        self._use_websocket_getter = use_websocket_getter or (lambda: True)
        self._subscribers: list[TickCallback] = []
        self._subscribers_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tick-fetch")
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._symbol: dict[str, str] | None = None
        self._seq = 0
        self._latest: TickSnapshot | None = None
        self._latest_lock = threading.Lock()
        self._last_emit_mono = 0.0
        self._emit_lock = threading.Lock()
        self._binance_stream: BinanceFuturesPriceStream | None = None
        self._cached_binance_tick: dict[str, Any] | None = None
        self._binance_tick_lock = threading.Lock()

    @property
    def latest(self) -> TickSnapshot | None:
        with self._latest_lock:
            return self._latest

    @property
    def uses_websocket(self) -> bool:
        return self._binance_stream is not None and self._binance_stream.is_running

    def subscribe(self, callback: TickCallback) -> None:
        with self._subscribers_lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: TickCallback) -> None:
        with self._subscribers_lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def start(self, symbol: dict[str, str]) -> None:
        self._symbol = dict(symbol)
        self.stop()
        self._stop_event.clear()
        self._last_emit_mono = 0.0

        if self._binance.is_connected and self._use_websocket_getter():
            self._binance_stream = BinanceFuturesPriceStream(
                use_demo=self._binance.use_demo,
                on_update=self._on_binance_stream_tick,
            )
            self._binance_stream.start(symbol["binance"])

        self._thread = threading.Thread(target=self._run_mt5_loop, name="tick-engine-mt5", daemon=True)
        self._thread.start()

    def update_symbol(self, symbol: dict[str, str]) -> None:
        self._symbol = dict(symbol)
        if self._binance_stream is not None and self._binance_stream.is_running:
            self._binance_stream.start(symbol["binance"])

    def stop(self) -> None:
        self._stop_event.set()
        if self._binance_stream is not None:
            self._binance_stream.stop()
            self._binance_stream = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        with self._binance_tick_lock:
            self._cached_binance_tick = None

    def shutdown(self) -> None:
        self.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _on_binance_stream_tick(self, tick: dict[str, Any]) -> None:
        with self._binance_tick_lock:
            self._cached_binance_tick = tick
        self._pool.submit(self._emit_snapshot, "binance_ws", tick)

    def _run_mt5_loop(self) -> None:
        while not self._stop_event.is_set():
            symbol = self._symbol
            if symbol is None:
                self._stop_event.wait(0.1)
                continue

            if not self._mt5.is_connected and not self._binance.is_connected:
                self._stop_event.wait(0.2)
                continue

            loop_started = time.perf_counter()

            if self.uses_websocket:
                with self._binance_tick_lock:
                    binance_tick = (
                        dict(self._cached_binance_tick) if self._cached_binance_tick else None
                    )
                if binance_tick is not None:
                    self._emit_snapshot("mt5_poll", binance_tick)
            else:
                self._emit_snapshot_rest(symbol)

            interval_ms = max(100, self._interval_ms_getter())
            elapsed_ms = (time.perf_counter() - loop_started) * 1000
            wait_sec = max(0.0, (interval_ms - elapsed_ms) / 1000.0)
            if self._stop_event.wait(wait_sec):
                break

    def _emit_snapshot_rest(self, symbol: dict[str, str]) -> None:
        fetch_started = time.perf_counter()
        mt5_tick: dict[str, Any] | None = None
        binance_tick: dict[str, Any] | None = None

        futures = []
        if self._mt5.is_connected:
            futures.append(("mt5", self._pool.submit(self._mt5.get_tick, symbol["mt5"])))
        if self._binance.is_connected:
            futures.append(
                ("binance", self._pool.submit(self._binance.get_ticker, symbol["binance"]))
            )

        for name, future in futures:
            try:
                result = future.result()
                if name == "mt5":
                    mt5_tick = result
                else:
                    binance_tick = result
            except Exception:
                pass

        fetch_duration_ms = (time.perf_counter() - fetch_started) * 1000
        self._publish_snapshot(symbol, mt5_tick, binance_tick, fetch_duration_ms, "rest_poll")

    def _emit_snapshot(self, source: str, binance_tick: dict[str, Any]) -> None:
        symbol = self._symbol
        if symbol is None:
            return

        min_emit_ms = self.WS_MIN_EMIT_MS if source == "binance_ws" else max(100, self._interval_ms_getter())
        with self._emit_lock:
            now = time.perf_counter()
            if (now - self._last_emit_mono) * 1000 < min_emit_ms:
                return

        if not self._mt5.is_connected:
            return

        fetch_started = time.perf_counter()
        try:
            mt5_tick = self._mt5.get_tick(symbol["mt5"])
        except Exception:
            mt5_tick = None
        fetch_duration_ms = (time.perf_counter() - fetch_started) * 1000

        self._publish_snapshot(symbol, mt5_tick, binance_tick, fetch_duration_ms, source)

    def _publish_snapshot(
        self,
        symbol: dict[str, str],
        mt5_tick: dict[str, Any] | None,
        binance_tick: dict[str, Any] | None,
        fetch_duration_ms: float,
        source: str,
    ) -> None:
        mt5_bid = float(mt5_tick["bid"]) if mt5_tick and mt5_tick.get("bid") is not None else None
        binance_price = (
            float(binance_tick["price"])
            if binance_tick and binance_tick.get("price") is not None
            else None
        )
        diff = None
        if mt5_bid is not None and binance_price is not None:
            diff = mt5_bid - binance_price

        with self._emit_lock:
            self._last_emit_mono = time.perf_counter()
            self._seq += 1
            seq = self._seq

        snapshot = TickSnapshot(
            seq=seq,
            ts_mono=time.perf_counter(),
            mt5_symbol=symbol["mt5"],
            binance_symbol=symbol["binance"],
            mt5_bid=mt5_bid,
            binance_price=binance_price,
            diff=diff,
            fetch_duration_ms=fetch_duration_ms,
            mt5_tick=mt5_tick,
            binance_tick=binance_tick,
            source=source,
        )

        with self._latest_lock:
            self._latest = snapshot

        self._notify(snapshot)

    def _notify(self, snapshot: TickSnapshot) -> None:
        with self._subscribers_lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(snapshot)
            except Exception:
                pass

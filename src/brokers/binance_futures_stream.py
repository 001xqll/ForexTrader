from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

PriceCallback = Callable[[dict[str, Any]], None]

# bookTicker = gyakori bid/ask; @ticker = utolsó kereskedési ár (ProTrader megfelelője)
MAINNET_STREAM_BASE = "wss://fstream.binance.com/stream"
DEMO_STREAM_BASE = "wss://fstream.binancefuture.com/stream"


class BinanceFuturesPriceStream:
    """Binance Futures websocket — bookTicker + 24h ticker kombinálva."""

    def __init__(self, use_demo: bool, on_update: PriceCallback) -> None:
        self._use_demo = use_demo
        self._on_update = on_update
        self._symbol = ""
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._latest: dict[str, Any] | None = None
        self._latest_lock = threading.Lock()
        self._connected = False
        self._bid: float | None = None
        self._ask: float | None = None
        self._last: float | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def latest(self) -> dict[str, Any] | None:
        with self._latest_lock:
            return dict(self._latest) if self._latest else None

    def start(self, symbol: str) -> None:
        self.stop()
        self._symbol = symbol.upper()
        self._bid = None
        self._ask = None
        self._last = None
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_forever,
            name=f"binance-ws-{self._symbol}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        self._connected = False
        self._bid = None
        self._ask = None
        self._last = None
        with self._latest_lock:
            self._latest = None

    def _run_forever(self) -> None:
        backoff_sec = 1.0
        while not self._stop_event.is_set():
            try:
                self._run_once()
                backoff_sec = 1.0
            except Exception:
                if self._stop_event.is_set():
                    break
                time.sleep(backoff_sec)
                backoff_sec = min(backoff_sec * 2, 30.0)

    def _merge_and_emit(self) -> None:
        if self._bid is None or self._ask is None:
            return
        price = self._last if self._last is not None else (self._bid + self._ask) / 2.0
        tick = {
            "symbol": self._symbol,
            "bid": self._bid,
            "ask": self._ask,
            "price": price,
            "source": "websocket",
        }
        with self._latest_lock:
            self._latest = tick
        self._on_update(tick)

    def _apply_book_ticker(self, data: dict[str, Any]) -> None:
        try:
            self._bid = float(data["b"])
            self._ask = float(data["a"])
        except (KeyError, TypeError, ValueError):
            return
        self._merge_and_emit()

    def _apply_symbol_ticker(self, data: dict[str, Any]) -> None:
        try:
            self._last = float(data["c"])
            if "b" in data and "a" in data:
                self._bid = float(data["b"])
                self._ask = float(data["a"])
        except (KeyError, TypeError, ValueError):
            return
        self._merge_and_emit()

    def _run_once(self) -> None:
        try:
            from websocket import WebSocketApp
        except ImportError as exc:
            raise RuntimeError(
                "A websocket-client csomag nincs telepítve. Futtasd: pip install websocket-client"
            ) from exc

        sym = self._symbol.lower()
        streams = f"{sym}@bookTicker/{sym}@ticker"
        base = DEMO_STREAM_BASE if self._use_demo else MAINNET_STREAM_BASE
        url = f"{base}?streams={streams}"

        def on_open(_ws) -> None:
            self._connected = True

        def on_close(_ws, _status, _msg) -> None:
            self._connected = False

        def on_error(_ws, _error) -> None:
            self._connected = False

        def on_message(_ws, message: str) -> None:
            if self._stop_event.is_set():
                return
            try:
                envelope = json.loads(message)
            except json.JSONDecodeError:
                return

            if "data" in envelope:
                data = envelope["data"]
                stream_name = str(envelope.get("stream", ""))
            else:
                data = envelope
                stream_name = ""

            if "@bookTicker" in stream_name:
                self._apply_book_ticker(data)
            elif "@ticker" in stream_name or data.get("e") == "24hrTicker":
                self._apply_symbol_ticker(data)
            elif "c" not in data and "b" in data and "a" in data:
                self._apply_book_ticker(data)

        ws = WebSocketApp(
            url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        ws.run_forever(ping_interval=20, ping_timeout=10)

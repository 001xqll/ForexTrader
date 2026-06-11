from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

PriceCallback = Callable[[dict[str, Any]], None]

# USD-M Futures market streams (bookTicker = valós idejű bid/ask)
MAINNET_WS_BASE = "wss://fstream.binance.com/ws"
DEMO_WS_BASE = "wss://fstream.binancefuture.com/ws"


class BinanceFuturesPriceStream:
    """Binance Futures bookTicker websocket — push alapú árfolyam."""

    def __init__(self, use_demo: bool, on_update: PriceCallback) -> None:
        self._use_demo = use_demo
        self._on_update = on_update
        self._symbol = ""
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._latest: dict[str, Any] | None = None
        self._latest_lock = threading.Lock()
        self._connected = False

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

    def _run_once(self) -> None:
        try:
            from websocket import WebSocketApp
        except ImportError as exc:
            raise RuntimeError(
                "A websocket-client csomag nincs telepítve. Futtasd: pip install websocket-client"
            ) from exc

        base = DEMO_WS_BASE if self._use_demo else MAINNET_WS_BASE
        stream = f"{self._symbol.lower()}@bookTicker"
        url = f"{base}/{stream}"

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
                data = json.loads(message)
                bid = float(data["b"])
                ask = float(data["a"])
            except (KeyError, TypeError, ValueError):
                return

            tick = {
                "symbol": str(data.get("s", self._symbol)).upper(),
                "bid": bid,
                "ask": ask,
                "price": bid,
                "source": "websocket",
            }
            with self._latest_lock:
                self._latest = tick
            self._on_update(tick)

        ws = WebSocketApp(
            url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        ws.run_forever(ping_interval=20, ping_timeout=10)

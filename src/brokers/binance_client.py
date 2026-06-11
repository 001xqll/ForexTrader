from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class BinanceConnectionResult:
    success: bool
    message: str


class BinanceFuturesClient:
    def __init__(self) -> None:
        self._client = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self, settings: dict[str, Any]) -> BinanceConnectionResult:
        try:
            from binance.client import Client
        except ImportError:
            return BinanceConnectionResult(
                success=False,
                message="A python-binance csomag nincs telepítve. Futtasd: pip install python-binance",
            )

        api_key = settings.get("api_key", "").strip()
        api_secret = settings.get("api_secret", "").strip()
        use_demo = bool(settings.get("demo", settings.get("testnet", True)))

        if not api_key or not api_secret:
            return BinanceConnectionResult(
                success=False,
                message="Binance: API kulcs és secret megadása kötelező.",
            )

        self.disconnect()

        try:
            client = self._create_client(api_key, api_secret, use_demo)
            client.timestamp_offset = 0
            server_time = client.get_server_time()
            client.timestamp_offset = server_time["serverTime"] - int(time.time() * 1000)
        except Exception as exc:  # noqa: BLE001 - show broker error in UI
            self._client = None
            self._connected = False
            return BinanceConnectionResult(
                success=False,
                message=f"Binance csatlakozás sikertelen: {exc}",
            )

        self._client = client
        self._connected = True

        mode = "Demo" if use_demo else "Éles"
        return BinanceConnectionResult(
            success=True,
            message=f"Binance Futures csatlakozás sikeres ({mode}).",
        )

    def disconnect(self) -> None:
        self._client = None
        self._connected = False

    def _create_client(self, api_key: str, api_secret: str, use_demo: bool):
        from binance.client import Client

        if use_demo:
            return Client(api_key, api_secret, demo=True)
        return Client(api_key, api_secret)

    def get_ticker(self, symbol: str) -> dict[str, Any] | None:
        if not self._connected or self._client is None or not symbol:
            return None

        try:
            ticker = self._client.futures_symbol_ticker(symbol=symbol.upper())
        except Exception:
            return None

        price = float(ticker["price"])
        return {
            "symbol": symbol.upper(),
            "price": price,
            "bid": price,
            "ask": price,
        }

    def get_daily_klines(self, symbol: str, limit: int = 30) -> list[list[Any]] | None:
        if not self._connected or self._client is None or not symbol:
            return None

        try:
            return self._client.futures_klines(
                symbol=symbol.upper(),
                interval="1d",
                limit=limit,
            )
        except Exception:
            return None

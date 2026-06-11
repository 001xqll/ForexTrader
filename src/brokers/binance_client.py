from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class BinanceConnectionResult:
    success: bool
    message: str
    account: dict[str, Any] | None = None


class BinanceFuturesClient:
    def __init__(self) -> None:
        self._client = None
        self._connected = False
        self._demo = True

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
            # Ugyanaz a módszer, mint a ProTrader-ben: python-binance + testnet flag.
            client = Client(api_key, api_secret, testnet=use_demo)
            client.timestamp_offset = 0
            server_time = client.get_server_time()
            client.timestamp_offset = server_time["serverTime"] - int(time.time() * 1000)
            balances = client.futures_account_balance()
        except Exception as exc:  # noqa: BLE001 - show broker error in UI
            self._client = None
            self._connected = False
            return BinanceConnectionResult(
                success=False,
                message=f"Binance csatlakozás sikertelen: {exc}",
            )

        self._client = client
        self._connected = True
        self._demo = use_demo

        account = self._format_balance(balances)
        mode = "Demo" if use_demo else "Éles"
        return BinanceConnectionResult(
            success=True,
            message=f"Binance Futures csatlakozás sikeres ({mode}).",
            account=account,
        )

    def disconnect(self) -> None:
        self._client = None
        self._connected = False

    def get_account_info(self) -> dict[str, Any] | None:
        if not self._connected or self._client is None:
            return None

        try:
            balances = self._client.futures_account_balance()
        except Exception:
            return None

        return self._format_balance(balances)

    def _format_balance(self, balances: list[dict[str, Any]]) -> dict[str, Any]:
        assets: dict[str, dict[str, float]] = {}
        usdt_total = 0.0
        usdt_free = 0.0
        usdt_used = 0.0

        for entry in balances:
            asset = entry.get("asset", "")
            balance = float(entry.get("balance") or 0)
            available = float(entry.get("availableBalance") or 0)
            if balance == 0 and available == 0:
                continue

            assets[asset] = {
                "total": balance,
                "free": available,
                "used": max(balance - available, 0.0),
            }

            if asset == "USDT":
                usdt_total = balance
                usdt_free = available
                usdt_used = max(balance - available, 0.0)

        return {
            "mode": "Demo" if self._demo else "Éles",
            "usdt_total": usdt_total,
            "usdt_free": usdt_free,
            "usdt_used": usdt_used,
            "assets": assets,
        }

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

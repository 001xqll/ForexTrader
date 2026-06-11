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
        self._use_demo = True
        self._use_websocket = True

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
        self._use_demo = use_demo
        self._use_websocket = bool(settings.get("use_websocket", True))

        mode = "Demo" if use_demo else "Éles"
        return BinanceConnectionResult(
            success=True,
            message=f"Binance Futures csatlakozás sikeres ({mode}).",
        )

    def disconnect(self) -> None:
        self._client = None
        self._connected = False

    @property
    def use_demo(self) -> bool:
        return self._use_demo

    @property
    def use_websocket(self) -> bool:
        return self._use_websocket

    def _create_client(self, api_key: str, api_secret: str, use_demo: bool):
        from binance.client import Client

        if use_demo:
            return Client(api_key, api_secret, demo=True)
        return Client(api_key, api_secret)

    def get_ticker(self, symbol: str) -> dict[str, Any] | None:
        if not self._connected or self._client is None or not symbol:
            return None

        symbol_upper = symbol.upper()
        try:
            book = self._client.futures_orderbook_ticker(symbol=symbol_upper)
            ticker = self._client.futures_symbol_ticker(symbol=symbol_upper)
        except Exception:
            return None

        bid = float(book["bidPrice"])
        ask = float(book["askPrice"])
        price = float(ticker["price"])
        return {
            "symbol": symbol_upper,
            "price": price,
            "bid": bid,
            "ask": ask,
            "source": "rest",
        }

    @staticmethod
    def _extract_fill_price(order: dict) -> float | None:
        avg_price = float(order.get("avgPrice") or 0)
        if avg_price > 0:
            return avg_price
        executed_qty = float(order.get("executedQty") or 0)
        cum_quote = float(order.get("cumQuote") or 0)
        if executed_qty > 0 and cum_quote > 0:
            return cum_quote / executed_qty
        return None

    def poll_fill_price(self, symbol: str, order: dict) -> float | None:
        fill_price = self._extract_fill_price(order)
        if fill_price is not None or self._client is None:
            return fill_price

        order_id = order.get("orderId")
        if not order_id:
            return None

        symbol_upper = symbol.upper()
        for _ in range(4):
            time.sleep(0.03)
            try:
                fresh = self._client.futures_get_order(
                    symbol=symbol_upper,
                    orderId=order_id,
                )
            except Exception:  # noqa: BLE001
                continue
            fill_price = self._extract_fill_price(fresh)
            if fill_price is not None:
                return fill_price
        return None

    def create_market_order(
        self, symbol: str, side: str, quantity: float
    ) -> tuple[bool, str, float | None, dict | None, float]:
        if not self._connected or self._client is None or not symbol or quantity <= 0:
            return False, "Binance nincs csatlakozva vagy érvénytelen paraméter.", None, None, 0.0

        side_upper = side.upper()
        if side_upper not in ("BUY", "SELL"):
            return False, f"Ismeretlen oldal: {side}", None, None, 0.0

        api_started = time.perf_counter()
        try:
            order = self._client.futures_create_order(
                symbol=symbol.upper(),
                side=side_upper,
                type="MARKET",
                quantity=quantity,
            )
        except Exception as exc:  # noqa: BLE001 - show broker error in UI
            api_ms = (time.perf_counter() - api_started) * 1000
            return False, f"Binance order hiba: {exc}", None, None, api_ms
        api_ms = (time.perf_counter() - api_started) * 1000
        fill_price = self._extract_fill_price(order)
        return True, f"Binance {side_upper} order sikeres.", fill_price, order, api_ms

    def rollback_open_leg(self, symbol: str, *, quantity: float, opened_side: str) -> tuple[bool, str]:
        if not self._connected or self._client is None or not symbol or quantity <= 0:
            return False, "Binance nincs csatlakozva vagy érvénytelen mennyiség."

        opened_upper = opened_side.upper()
        if opened_upper not in ("BUY", "SELL"):
            return False, f"Ismeretlen oldal: {opened_side}"

        close_side = "SELL" if opened_upper == "BUY" else "BUY"
        try:
            self._client.futures_create_order(
                symbol=symbol.upper(),
                side=close_side,
                type="MARKET",
                quantity=quantity,
                reduceOnly=True,
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"Binance visszagörgetés hiba: {exc}"
        return True, f"Binance visszagörgetés: {close_side} {quantity}"

    def close_all_positions(self, symbol: str) -> tuple[bool, str]:
        if not self._connected or self._client is None or not symbol:
            return False, "Binance nincs csatlakozva."

        try:
            positions = self._client.futures_position_information(symbol=symbol.upper())
        except Exception as exc:  # noqa: BLE001
            return False, f"Binance pozíció lekérés hiba: {exc}"

        closed_any = False
        for pos in positions:
            amount = float(pos.get("positionAmt") or 0)
            if amount == 0:
                continue
            side = "SELL" if amount > 0 else "BUY"
            qty = abs(amount)
            try:
                self._client.futures_create_order(
                    symbol=symbol.upper(),
                    side=side,
                    type="MARKET",
                    quantity=qty,
                )
                closed_any = True
            except Exception as exc:  # noqa: BLE001
                return False, f"Binance zárás hiba: {exc}"

        if not closed_any:
            return True, "Nincs nyitott Binance pozíció."
        return True, "Binance pozíciók zárva."

    def count_position_units(self, symbol: str, unit_size: float) -> int:
        if not self._connected or self._client is None or not symbol or unit_size <= 0:
            return 0
        try:
            positions = self._client.futures_position_information(symbol=symbol.upper())
        except Exception:
            return 0

        total_qty = 0.0
        for pos in positions:
            total_qty += abs(float(pos.get("positionAmt") or 0))
        if total_qty == 0:
            return 0
        return int(round(total_qty / unit_size))

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

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


@dataclass
class MT5ConnectionResult:
    success: bool
    message: str


class MT5Client:
    def __init__(self) -> None:
        self._connected = False
        self._api_lock = threading.RLock()

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self, settings: dict[str, Any]) -> MT5ConnectionResult:
        try:
            import MetaTrader5 as mt5
        except ImportError:
            return MT5ConnectionResult(
                success=False,
                message="A MetaTrader5 csomag nincs telepítve. Futtasd: pip install MetaTrader5",
            )

        login = int(settings.get("login") or 0)
        password = settings.get("password", "")
        server = settings.get("server", "")
        terminal_path = settings.get("terminal_path", "").strip() or None

        if not login or not password or not server:
            return MT5ConnectionResult(
                success=False,
                message="MT5: login, jelszó és szerver megadása kötelező.",
            )

        with self._api_lock:
            if self._connected:
                mt5.shutdown()

            init_kwargs: dict[str, Any] = {}
            if terminal_path:
                init_kwargs["path"] = terminal_path

            if not mt5.initialize(**init_kwargs):
                error = mt5.last_error()
                return MT5ConnectionResult(
                    success=False,
                    message=f"MT5 inicializálás sikertelen: {error}",
                )

            authorized = mt5.login(login=login, password=password, server=server)
            if not authorized:
                error = mt5.last_error()
                mt5.shutdown()
                self._connected = False
                return MT5ConnectionResult(
                    success=False,
                    message=f"MT5 bejelentkezés sikertelen: {error}",
                )

            self._connected = True
            return MT5ConnectionResult(
                success=True,
                message="MT5 csatlakozás sikeres.",
            )

    def disconnect(self) -> None:
        with self._api_lock:
            if not self._connected:
                return

            try:
                import MetaTrader5 as mt5

                mt5.shutdown()
            finally:
                self._connected = False

    def get_tick(self, symbol: str) -> dict[str, Any] | None:
        if not self._connected or not symbol:
            return None

        import MetaTrader5 as mt5

        with self._api_lock:
            info = mt5.symbol_info(symbol)
            if info is None:
                return None

            if not info.visible and not mt5.symbol_select(symbol, True):
                return None

            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return None

            digits = info.digits
            return {
                "symbol": symbol,
                "bid": tick.bid,
                "ask": tick.ask,
                "last": tick.last,
                "digits": digits,
            }

    def get_daily_rates(self, symbol: str, count: int = 30) -> list[dict[str, Any]] | None:
        if not self._connected or not symbol:
            return None

        import MetaTrader5 as mt5

        with self._api_lock:
            info = mt5.symbol_info(symbol)
            if info is None:
                return None

            if not info.visible and not mt5.symbol_select(symbol, True):
                return None

            rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, count)
            if rates is None:
                return None

            return [
                {
                    "time": int(rate["time"]),
                    "open": float(rate["open"]),
                    "high": float(rate["high"]),
                    "low": float(rate["low"]),
                    "close": float(rate["close"]),
                }
                for rate in rates
            ]

    def open_market_order(self, symbol: str, side: str, volume: float) -> tuple[bool, str, float | None]:
        if not self._connected or not symbol or volume <= 0:
            return False, "MT5 nincs csatlakozva vagy érvénytelen paraméter.", None

        import MetaTrader5 as mt5

        with self._api_lock:
            info = mt5.symbol_info(symbol)
            if info is None:
                return False, f"Ismeretlen szimbólum: {symbol}", None

            if not info.visible and not mt5.symbol_select(symbol, True):
                return False, f"Szimbólum nem választható: {symbol}", None

            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return False, "Nincs tick adat.", None

            side_upper = side.upper()
            if side_upper == "BUY":
                order_type = mt5.ORDER_TYPE_BUY
                price = tick.ask
            elif side_upper == "SELL":
                order_type = mt5.ORDER_TYPE_SELL
                price = tick.bid
            else:
                return False, f"Ismeretlen oldal: {side}", None

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(volume),
                "type": order_type,
                "price": price,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result is None:
                return False, f"MT5 order_send hiba: {mt5.last_error()}", None

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return False, f"MT5 order elutasítva: {result.comment} ({result.retcode})", None

            fill_price = float(result.price) if result.price else None
            return True, "MT5 order sikeres.", fill_price

    def close_all_positions(self, symbol: str) -> tuple[bool, str]:
        if not self._connected or not symbol:
            return False, "MT5 nincs csatlakozva."

        import MetaTrader5 as mt5

        with self._api_lock:
            positions = mt5.positions_get(symbol=symbol)
            if not positions:
                return True, "Nincs nyitott MT5 pozíció."

            for pos in positions:
                tick = mt5.symbol_info_tick(symbol)
                if tick is None:
                    return False, "Nincs tick adat záráshoz."

                if pos.type == mt5.ORDER_TYPE_BUY:
                    order_type = mt5.ORDER_TYPE_SELL
                    price = tick.bid
                else:
                    order_type = mt5.ORDER_TYPE_BUY
                    price = tick.ask

                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": pos.volume,
                    "type": order_type,
                    "position": pos.ticket,
                    "price": price,
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                result = mt5.order_send(request)
                if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                    comment = result.comment if result else mt5.last_error()
                    return False, f"MT5 zárás sikertelen: {comment}"

            return True, "MT5 pozíciók zárva."

    def count_positions(self, symbol: str) -> int:
        if not self._connected or not symbol:
            return 0
        import MetaTrader5 as mt5

        with self._api_lock:
            positions = mt5.positions_get(symbol=symbol)
            return len(positions) if positions else 0

    def primary_position_side(self, symbol: str) -> str | None:
        if not self._connected or not symbol:
            return None
        import MetaTrader5 as mt5

        with self._api_lock:
            positions = mt5.positions_get(symbol=symbol)
            if not positions:
                return None
            if positions[0].type == mt5.ORDER_TYPE_SELL:
                return "SELL"
            return "BUY"

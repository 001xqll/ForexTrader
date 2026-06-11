from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MT5ConnectionResult:
    success: bool
    message: str
    account: dict[str, Any] | None = None


class MT5Client:
    def __init__(self) -> None:
        self._connected = False

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
        account = self.get_account_info()
        return MT5ConnectionResult(
            success=True,
            message="MT5 csatlakozás sikeres.",
            account=account,
        )

    def disconnect(self) -> None:
        if not self._connected:
            return

        try:
            import MetaTrader5 as mt5

            mt5.shutdown()
        finally:
            self._connected = False

    def get_account_info(self) -> dict[str, Any] | None:
        if not self._connected:
            return None

        import MetaTrader5 as mt5

        info = mt5.account_info()
        if info is None:
            return None

        return {
            "login": info.login,
            "name": info.name,
            "server": info.server,
            "currency": info.currency,
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.margin_free,
            "margin_level": info.margin_level,
            "leverage": info.leverage,
            "profit": info.profit,
        }

    def get_tick(self, symbol: str) -> dict[str, Any] | None:
        if not self._connected or not symbol:
            return None

        import MetaTrader5 as mt5

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

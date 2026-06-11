from __future__ import annotations

from collections.abc import Callable

from src.brokers.binance_client import BinanceFuturesClient
from src.brokers.mt5_client import MT5Client
from src.config.settings import get_price_refresh_ms, load_config
from src.trading.order_executor import OrderExecutor
from src.trading.strategy_engine import StrategyEngine
from src.trading.tick_engine import TickEngine
from src.trading.tick_snapshot import TickSnapshot

LogFn = Callable[[str], None]
TradingAllowedFn = Callable[[], bool]


class TradingRuntime:
    """Tick motor + stratégia + order végrehajtó összekötése."""

    def __init__(
        self,
        mt5: MT5Client,
        binance: BinanceFuturesClient,
        log: LogFn,
        is_trading_allowed: TradingAllowedFn,
    ) -> None:
        self._log = log
        self.order_executor = OrderExecutor(mt5, binance, log, dry_run=True)
        self.strategy_engine = StrategyEngine(
            self.order_executor,
            is_trading_allowed,
            log,
        )
        self.tick_engine = TickEngine(
            mt5,
            binance,
            interval_ms_getter=lambda: get_price_refresh_ms(load_config()),
            use_websocket_getter=lambda: bool(
                load_config().get("binance", {}).get("use_websocket", True)
            ),
        )
        self.tick_engine.subscribe(self.strategy_engine.on_tick)

    def subscribe_ticks(self, callback: Callable[[TickSnapshot], None]) -> None:
        self.tick_engine.subscribe(callback)

    def unsubscribe_ticks(self, callback: Callable[[TickSnapshot], None]) -> None:
        self.tick_engine.unsubscribe(callback)

    def start(self, symbol: dict[str, str]) -> None:
        self.tick_engine.start(symbol)
        binance_mode = "websocket" if self.tick_engine.uses_websocket else "REST poll"
        self._log(
            f"Tick motor elindult ({symbol['mt5']} / {symbol['binance']}, "
            f"MT5 poll {get_price_refresh_ms(load_config())} ms, Binance: {binance_mode})."
        )

    def update_symbol(self, symbol: dict[str, str]) -> None:
        self.tick_engine.update_symbol(symbol)
        self._log(f"Tick motor szimbólum: {symbol['mt5']} / {symbol['binance']}.")

    def stop(self) -> None:
        self.tick_engine.stop()
        self._log("Tick motor leállítva.")

    def shutdown(self) -> None:
        self.tick_engine.shutdown()
        self.order_executor.shutdown()

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from src.config.settings import get_strategy_config
from src.logger.app_logger import log_warning
from src.trading.level_state import LevelHitState
from src.trading.order_executor import OrderExecutor
from src.trading.tick_snapshot import TickSnapshot

LogFn = Callable[[str], None]
TradingAllowedFn = Callable[[], bool]
MismatchCallback = Callable[[bool], None]


class StrategyEngine:
    """Spread szint stratégia — ProTrader logika alapján."""

    MISMATCH_RECHECK_SEC = 30.0
    SPREAD_BLOCK_LOG_SEC = 30.0

    def __init__(
        self,
        order_executor: OrderExecutor,
        is_trading_allowed: TradingAllowedFn,
        log: LogFn,
        mt5_client: Any,
        binance_client: Any,
    ) -> None:
        self._order_executor = order_executor
        self._is_trading_allowed = is_trading_allowed
        self._log = log
        self._mt5 = mt5_client
        self._binance = binance_client
        self._levels_hit = LevelHitState()
        self._order_in_flight = False
        self._symbol: dict[str, str] | None = None
        self._position_mismatch = False
        self._mismatch_reason = ""
        self._last_mismatch_recheck_mono = 0.0
        self._last_spread_block_log_mono = 0.0
        self._post_sl_lock = False
        self._mismatch_callback: MismatchCallback | None = None

    @property
    def levels_hit(self) -> LevelHitState:
        return self._levels_hit

    @property
    def position_mismatch(self) -> bool:
        return self._position_mismatch

    @property
    def mismatch_reason(self) -> str:
        return self._mismatch_reason

    def set_mismatch_callback(self, callback: MismatchCallback | None) -> None:
        self._mismatch_callback = callback

    def reset_levels(self) -> None:
        self._levels_hit.clear()
        self._post_sl_lock = False

    def clear_trading_block(self) -> None:
        self._set_position_mismatch(False)
        self._mismatch_reason = ""
        self._symbol = None
        self._post_sl_lock = False

    def _set_position_mismatch(self, active: bool, *, reason: str = "") -> None:
        if active == self._position_mismatch and (not active or reason == self._mismatch_reason):
            return
        self._position_mismatch = active
        self._mismatch_reason = reason if active else ""
        if self._mismatch_callback is not None:
            self._mismatch_callback(active)

    def sync_levels_from_exchange(self, symbol: dict[str, str]) -> None:
        if not self._mt5.is_connected or not self._binance.is_connected:
            return

        self._symbol = dict(symbol)
        strategy = get_strategy_config()
        levels = strategy["levels"]
        unit = float(strategy["binance_quantity"])

        mt5_count = self._mt5.count_positions(symbol["mt5"])
        binance_count = self._binance.count_position_units(symbol["binance"], unit)

        if mt5_count == 0 and binance_count == 0:
            if self._position_mismatch:
                self._log("Pozíció eltérés megszűnt — kereskedés folytatódik.")
            self._set_position_mismatch(False)
            self._levels_hit.clear()
            self._post_sl_lock = False
            self._log("Nincs nyitott pozíció — szintek törölve.")
            return

        if mt5_count != binance_count:
            reason = f"MT5 ({mt5_count}) ≠ Binance ({binance_count})"
            entering = not self._position_mismatch
            self._set_position_mismatch(True, reason=reason)
            self._levels_hit.clear()
            if entering:
                log_warning(
                    f"MT5 pozíciók ({mt5_count}) ≠ Binance ({binance_count}) — "
                    "ellenőrizd manuálisan! Kereskedés tiltva."
                )
            return

        if self._position_mismatch:
            self._log("Pozíció eltérés megszűnt — kereskedés folytatódik.")
        self._set_position_mismatch(False)

        self._levels_hit.clear()
        side = self._mt5.primary_position_side(symbol["mt5"])
        direction = "pos" if side == "SELL" else "neg"
        restore_count = min(mt5_count, len(levels))
        for index in range(restore_count):
            self._levels_hit.mark_hit(levels[index], direction)

        self._log(
            f"Visszaállítva {restore_count} szint: {self._levels_hit.keys()}"
        )

    def _recheck_mismatch_if_due(self) -> None:
        if not self._position_mismatch or self._symbol is None:
            return
        now = time.perf_counter()
        if now - self._last_mismatch_recheck_mono < self.MISMATCH_RECHECK_SEC:
            return
        self._last_mismatch_recheck_mono = now
        self.sync_levels_from_exchange(self._symbol)

    def on_tick(self, snapshot: TickSnapshot) -> None:
        if not snapshot.is_complete:
            return
        if self._position_mismatch:
            self._recheck_mismatch_if_due()
            return
        self._evaluate(snapshot)

    def _evaluate(self, snapshot: TickSnapshot) -> None:
        if not self._is_trading_allowed():
            return
        if self._order_in_flight:
            return

        strategy = get_strategy_config()
        self._order_executor.dry_run = bool(strategy.get("dry_run", True))

        base = float(strategy["base"])
        exit_threshold = float(strategy["exit_threshold"])
        stop_loss = float(strategy["stop_loss"])
        levels: list[float] = list(strategy["levels"])
        lot_mt5 = float(strategy["lot_mt5"])
        binance_qty = float(strategy["binance_quantity"])
        diff = float(snapshot.diff or 0)
        dist_from_base = diff - base

        if self._post_sl_lock and abs(dist_from_base) <= exit_threshold:
            self._post_sl_lock = False
            if not self._order_executor.dry_run:
                self._log(
                    f"[Stop-loss] A diff visszatért a bázis közelébe "
                    f"(távolság={dist_from_base:+.2f}, küszöb=±{exit_threshold:g}) — "
                    "újra nyitható."
                )

        if (
            stop_loss > 0
            and self._levels_hit.keys()
            and abs(dist_from_base) >= stop_loss
        ):
            if not self._spread_allows_trading(snapshot, strategy):
                self._log_spread_blocked(snapshot, strategy, "stop-loss zárás")
                return
            self._stop_loss_close(snapshot, base, dist_from_base, stop_loss)
            return

        if self._levels_hit.has_direction("pos") and dist_from_base <= exit_threshold:
            if not self._spread_allows_trading(snapshot, strategy):
                self._log_spread_blocked(snapshot, strategy, "take-profit zárás")
                return
            self._close_direction(snapshot, "pos", base, dist_from_base, exit_threshold)
            return

        if self._levels_hit.has_direction("neg") and dist_from_base >= -exit_threshold:
            if not self._spread_allows_trading(snapshot, strategy):
                self._log_spread_blocked(snapshot, strategy, "take-profit zárás")
                return
            self._close_direction(snapshot, "neg", base, dist_from_base, exit_threshold)
            return

        if self._post_sl_lock:
            return

        if stop_loss > 0 and abs(dist_from_base) >= stop_loss:
            return

        for level in levels:
            if diff >= base + level and not self._levels_hit.is_hit(level, "pos"):
                if not self._spread_allows_trading(snapshot, strategy):
                    self._log_spread_blocked(snapshot, strategy, "nyitás")
                    return
                self._open_at_level(snapshot, level, "pos", lot_mt5, binance_qty, base)

            if diff <= base - level and not self._levels_hit.is_hit(level, "neg"):
                if not self._spread_allows_trading(snapshot, strategy):
                    self._log_spread_blocked(snapshot, strategy, "nyitás")
                    return
                self._open_at_level(snapshot, level, "neg", lot_mt5, binance_qty, base)

    @staticmethod
    def _spread_allows_trading(snapshot: TickSnapshot, strategy: dict) -> bool:
        max_mt5 = float(strategy["mt5_max_spread"])
        max_binance = float(strategy["binance_max_spread"])
        if snapshot.mt5_spread is None or snapshot.binance_spread is None:
            return False
        return snapshot.mt5_spread <= max_mt5 and snapshot.binance_spread <= max_binance

    def _log_spread_blocked(
        self,
        snapshot: TickSnapshot,
        strategy: dict,
        action: str,
    ) -> None:
        if self._order_executor.dry_run:
            return
        now = time.perf_counter()
        if now - self._last_spread_block_log_mono < self.SPREAD_BLOCK_LOG_SEC:
            return
        self._last_spread_block_log_mono = now
        max_mt5 = float(strategy["mt5_max_spread"])
        max_binance = float(strategy["binance_max_spread"])
        mt5_text = f"{snapshot.mt5_spread:.2f}" if snapshot.mt5_spread is not None else "—"
        binance_text = (
            f"{snapshot.binance_spread:.2f}" if snapshot.binance_spread is not None else "—"
        )
        self._log(
            f"[Spread] {action} kihagyva — MT5: {mt5_text} (max {max_mt5:g}), "
            f"Binance: {binance_text} (max {max_binance:g})"
        )

    @staticmethod
    def _format_diff_prices(snapshot: TickSnapshot) -> str:
        mt5_text = f"{snapshot.mt5_bid:.2f}" if snapshot.mt5_bid is not None else "—"
        binance_text = (
            f"{snapshot.binance_price:.2f}" if snapshot.binance_price is not None else "—"
        )
        return f" MT5_bid={mt5_text} Binance={binance_text}"

    def _open_at_level(
        self,
        snapshot: TickSnapshot,
        level: float,
        direction: str,
        lot_mt5: float,
        binance_qty: float,
        base: float,
    ) -> None:
        dry_run = self._order_executor.dry_run
        self._order_in_flight = True

        if not dry_run:
            signal_to_order_ms = (time.perf_counter() - snapshot.ts_mono) * 1000
            prices = self._format_diff_prices(snapshot)
            if direction == "pos":
                self._log(
                    f"[Szint +{level:g}] diff={snapshot.diff:+.2f} (bázis={base:.2f}){prices} → "
                    f"MT5 SHORT + Binance LONG | tick→jel={signal_to_order_ms:.1f} ms"
                )
            else:
                self._log(
                    f"[Szint -{level:g}] diff={snapshot.diff:+.2f} (bázis={base:.2f}){prices} → "
                    f"MT5 LONG + Binance SHORT | tick→jel={signal_to_order_ms:.1f} ms"
                )

        try:
            result = self._order_executor.open_level_hedge(
                snapshot,
                direction=direction,
                level=level,
                lot_mt5=lot_mt5,
                binance_qty=binance_qty,
            )
            if result.mt5.success and result.binance.success:
                self._levels_hit.mark_hit(level, direction)
        finally:
            self._order_in_flight = False

    def _stop_loss_close(
        self,
        snapshot: TickSnapshot,
        base: float,
        dist_from_base: float,
        stop_loss: float,
    ) -> None:
        dry_run = self._order_executor.dry_run
        self._order_in_flight = True
        if not dry_run:
            prices = self._format_diff_prices(snapshot)
            log_warning(
                f"!!! STOP-LOSS !!! diff={snapshot.diff:+.2f} (bázis={base:.2f}, "
                f"távolság={dist_from_base:+.2f}, limit=±{stop_loss:g}){prices}"
            )
        try:
            direction = "pos" if self._levels_hit.has_direction("pos") else "neg"
            result = self._order_executor.close_hedge_pair(snapshot, direction=direction)
            if result.success:
                self._levels_hit.clear()
                self._post_sl_lock = True
                if not dry_run:
                    exit_threshold = float(get_strategy_config()["exit_threshold"])
                    self._log(
                        "[Stop-loss] Összes pozíció zárva — újranyitás tiltva, "
                        f"várakozás a bázis ±{exit_threshold:g} zónáig."
                    )
        finally:
            self._order_in_flight = False

    def _close_direction(
        self,
        snapshot: TickSnapshot,
        direction: str,
        base: float,
        dist_from_base: float,
        exit_threshold: float,
    ) -> None:
        dry_run = self._order_executor.dry_run
        self._order_in_flight = True
        if not dry_run:
            prices = self._format_diff_prices(snapshot)
            log_warning(
                f"!!! TAKE-PROFIT !!! diff={snapshot.diff:+.2f} (bázis={base:.2f}, "
                f"távolság={dist_from_base:+.2f}, limit=±{exit_threshold:g}){prices}"
            )
        try:
            result = self._order_executor.close_hedge_pair(snapshot, direction=direction)
            if result.success:
                self._levels_hit.clear_direction(direction)
                if not dry_run:
                    self._log("[ZÁRÁS] szintek törölve")
        finally:
            self._order_in_flight = False

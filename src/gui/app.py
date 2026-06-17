from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk

from src.brokers.binance_client import BinanceFuturesClient
from src.brokers.mt5_client import MT5Client
from src.config.settings import (
    get_chart_refresh_ms,
    get_price_refresh_ms,
    get_selected_symbol,
    get_strategy_config,
    get_symbol_pairs,
    load_config,
    save_config,
)
from src.data.diff_history import build_diff_dataframe
from src.gui.diff_chart_panel import DiffChartPanel
from src.gui.log_viewer_dialog import LogViewerDialog
from src.gui.settings_dialog import SettingsDialog
from src.logger.app_logger import flush_logs
from src.logger.app_logger import log as app_log
from src.logger.app_logger import setup_logger
from src.trading.market_hours import is_market_open
from src.trading.runtime import TradingRuntime
from src.trading.tick_snapshot import TickSnapshot


class TradingApp(tk.Tk):
    LOG_FONT = ("Consolas", 11)
    LOG_WARNING_FONT = ("Consolas", 11, "bold")
    STATUS_OK_COLOR = "#16a34a"
    STATUS_ERROR_COLOR = "#b91c1c"
    MARKET_CHECK_SEC = 30
    DEFAULT_WIDTH = 1120
    DEFAULT_HEIGHT = 1000
    MIN_WIDTH = 1020
    MIN_HEIGHT = 860

    def __init__(self) -> None:
        super().__init__()
        self.title("ForexTrader")
        self.geometry(f"{self.DEFAULT_WIDTH}x{self.DEFAULT_HEIGHT}")
        self.minsize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._mt5 = MT5Client()
        self._binance = BinanceFuturesClient()
        self._runtime = TradingRuntime(
            self._mt5,
            self._binance,
            log=self._log,
            is_trading_allowed=lambda: self.is_trading_allowed,
        )
        self._runtime.subscribe_ticks(self._on_tick_snapshot)
        self._runtime.set_main_thread_scheduler(lambda fn: self.after(0, fn))
        self._runtime.strategy_engine.set_mismatch_callback(
            lambda _blocked: self.after(0, self._refresh_status_labels)
        )

        self._busy = False
        self._symbol_var = tk.StringVar()
        self._last_market_open: bool | None = None
        self._market_job: str | None = None
        self._runtime_active = False

        self._build_ui()
        self._init_logging()
        self._reload_symbol_selector()
        self._apply_chart_strategy_levels()
        self._refresh_status_labels()
        self._check_market_status(force_log=True)
        self._start_market_watch()

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=12)
        header.pack(fill="x")

        ttk.Label(header, text="ForexTrader", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            header,
            text="MT5 és Binance Futures — tick motor + különbségi D1 grafikon.",
            foreground="#555555",
        ).pack(anchor="w", pady=(4, 0))

        status_frame = ttk.LabelFrame(self, text="Állapot", padding=12)
        status_frame.pack(fill="x", padx=12, pady=(0, 8))

        self._mt5_status = ttk.Label(status_frame, text="MT5: nincs csatlakozva")
        self._binance_status = ttk.Label(status_frame, text="Binance: nincs csatlakozva")
        self._market_status = ttk.Label(status_frame, text="Kereskedés: —")
        self._mt5_status.pack(anchor="w", pady=2)
        self._binance_status.pack(anchor="w", pady=2)
        self._market_status.pack(anchor="w", pady=2)

        buttons = ttk.Frame(self, padding=(12, 0, 12, 8))
        buttons.pack(fill="x")

        self._settings_btn = ttk.Button(
            buttons,
            text="Beállítások",
            command=self._open_settings,
        )
        self._connect_btn = ttk.Button(buttons, text="Connect", command=self._connect)
        self._disconnect_btn = ttk.Button(
            buttons,
            text="Disconnect",
            command=self._disconnect,
        )
        self._settings_btn.pack(side="left")
        self._connect_btn.pack(side="left", padx=(8, 0))
        self._disconnect_btn.pack(side="left", padx=(8, 0))

        chart_frame = ttk.LabelFrame(self, text="Különbségi árfolyam (30 nap, D1)", padding=8)
        chart_frame.pack(fill="x", padx=12, pady=(0, 8))

        selector_row = ttk.Frame(chart_frame)
        selector_row.pack(fill="x", pady=(0, 8))
        ttk.Label(selector_row, text="Szimbólum:").pack(side="left")
        self._symbol_combo = ttk.Combobox(
            selector_row,
            textvariable=self._symbol_var,
            state="readonly",
            width=42,
        )
        self._symbol_combo.pack(side="left", padx=(8, 0))
        self._symbol_combo.bind("<<ComboboxSelected>>", self._on_symbol_changed)

        self._chart = DiffChartPanel(chart_frame)
        self._chart.pack(anchor="nw")

        log_frame = ttk.LabelFrame(self, text="Napló", padding=12)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        log_toolbar = ttk.Frame(log_frame)
        log_toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(log_toolbar, text="Előzmények", command=self._open_log_viewer).pack(side="left")

        self._log_text = scrolledtext.ScrolledText(
            log_frame,
            height=12,
            wrap="none",
            font=self.LOG_FONT,
        )
        self._log_hscroll = ttk.Scrollbar(
            log_frame,
            orient="horizontal",
            command=self._log_text.xview,
        )
        self._log_text.configure(xscrollcommand=self._log_hscroll.set)
        self._log_text.tag_configure(
            "log_warning",
            foreground="#b91c1c",
            font=self.LOG_WARNING_FONT,
        )
        self._log_text.tag_configure("log_warning_sep", foreground="#b91c1c")
        self._log_text.pack(fill="both", expand=True)
        self._log_hscroll.pack(fill="x")
        self._log_text.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self._connect_btn.configure(state=state)
        self._disconnect_btn.configure(state=state)

    def _init_logging(self) -> None:
        setup_logger(gui_callback=self._append_log_line)
        self._log("ForexTrader elindult.")

    def _append_log_line(self, line: str, level: int = logging.INFO) -> None:
        def write() -> None:
            self._log_text.configure(state="normal")
            if level >= logging.WARNING:
                separator = "─" * 72
                self._log_text.insert("end", separator + "\n", ("log_warning_sep",))
                self._log_text.insert("end", "  !!! FIGYELEM !!!\n", ("log_warning",))
                self._log_text.insert("end", line + "\n", ("log_warning",))
                self._log_text.insert("end", separator + "\n", ("log_warning_sep",))
            else:
                self._log_text.insert("end", line + "\n")
            self._log_text.see("end")
            self._log_text.configure(state="disabled")

        self.after(0, write)

    def _open_log_viewer(self) -> None:
        LogViewerDialog(self)

    def _log(self, message: str) -> None:
        app_log(message)

    def _refresh_status_labels(self) -> None:
        mt5_connected = self._mt5.is_connected
        binance_connected = self._binance.is_connected
        mt5_text = "MT5: csatlakozva" if mt5_connected else "MT5: nincs csatlakozva"
        binance_text = (
            "Binance: csatlakozva" if binance_connected else "Binance: nincs csatlakozva"
        )
        self._mt5_status.configure(
            text=mt5_text,
            foreground=self.STATUS_OK_COLOR if mt5_connected else self.STATUS_ERROR_COLOR,
        )
        self._binance_status.configure(
            text=binance_text,
            foreground=self.STATUS_OK_COLOR if binance_connected else self.STATUS_ERROR_COLOR,
        )
        self._update_market_status_label()

    @property
    def is_trading_allowed(self) -> bool:
        return is_market_open(load_config())

    def _update_market_status_label(self) -> None:
        if self._runtime_active and self._runtime.strategy_engine.position_mismatch:
            reason = self._runtime.strategy_engine.mismatch_reason
            self._market_status.configure(
                text=f"Kereskedés: TILTVA (pozíció eltérés: {reason})",
                foreground=self.STATUS_ERROR_COLOR,
            )
            return

        config = load_config()
        if bool(get_strategy_config(config).get("dry_run", True)):
            self._market_status.configure(
                text="Kereskedés: nem éles kereskedés (dry-run)",
                foreground=self.STATUS_ERROR_COLOR,
            )
            return

        market_cfg = config.get("market_hours", {})
        if not market_cfg.get("enabled", True):
            self._market_status.configure(
                text="Kereskedés: nincs időkorlát (ellenőrzés kikapcsolva)",
                foreground=self.STATUS_OK_COLOR,
            )
            return

        if self.is_trading_allowed:
            self._market_status.configure(
                text="Kereskedés: engedélyezve (piac nyitva)",
                foreground=self.STATUS_OK_COLOR,
            )
        else:
            self._market_status.configure(
                text="Kereskedés: várakozás piacnyitásra",
                foreground=self.STATUS_ERROR_COLOR,
            )

    def _start_market_watch(self) -> None:
        self._stop_market_watch()
        self._schedule_market_watch()

    def _stop_market_watch(self) -> None:
        if self._market_job is not None:
            self.after_cancel(self._market_job)
            self._market_job = None

    def _schedule_market_watch(self) -> None:
        self._market_job = self.after(self.MARKET_CHECK_SEC * 1000, self._on_market_watch_tick)

    def _on_market_watch_tick(self) -> None:
        self._check_market_status()
        self._schedule_market_watch()

    def _check_market_status(self, force_log: bool = False) -> None:
        config = load_config()
        market_cfg = config.get("market_hours", {})

        if not market_cfg.get("enabled", True):
            self._last_market_open = True
            self._update_market_status_label()
            return

        is_open = self.is_trading_allowed
        if force_log or self._last_market_open is None or is_open != self._last_market_open:
            if is_open:
                self._log("Kereskedés engedélyezve — piac nyitva.")
            else:
                self._log("Várakozás piacnyitásra — a piac jelenleg zárva.")
            self._last_market_open = is_open

        self._update_market_status_label()

    def _reload_symbol_selector(self) -> None:
        config = load_config()
        pairs = get_symbol_pairs(config)
        labels = [f"{pair['name']}  ({pair['mt5']} / {pair['binance']})" for pair in pairs]
        self._symbol_combo["values"] = labels

        selected = get_selected_symbol(config)
        if selected and labels:
            index = pairs.index(selected)
            self._symbol_var.set(labels[index])
        elif labels:
            self._symbol_var.set(labels[0])
        else:
            self._symbol_var.set("")

    def _on_symbol_changed(self, _event: tk.Event | None = None) -> None:
        config = load_config()
        pairs = get_symbol_pairs(config)
        labels = [f"{pair['name']}  ({pair['mt5']} / {pair['binance']})" for pair in pairs]

        try:
            index = labels.index(self._symbol_var.get())
        except ValueError:
            return

        config.setdefault("symbols", {})["selected_index"] = index
        save_config(config)
        self._load_history_if_needed(force=True)
        if self._runtime_active:
            symbol = self._get_current_symbol()
            if symbol:
                self._runtime.update_symbol(symbol)

    def _get_current_symbol(self) -> dict[str, str] | None:
        return get_selected_symbol(load_config())

    @staticmethod
    def _history_load_error(
        symbol: dict[str, str],
        mt5_rates: list | None,
        binance_klines: list | None,
        df_diff,
    ) -> str:
        mt5_sym = symbol.get("mt5", "")
        bin_sym = symbol.get("binance", "")
        if not mt5_rates:
            return (
                f"MT5 D1 adat hiányzik ({mt5_sym}) — ellenőrizd a szimbólumnevet "
                "és hogy az MT5 terminálban van-e előzmény (nyisd meg a napi grafikont)."
            )
        if not binance_klines:
            return (
                f"Binance D1 adat hiányzik ({bin_sym}) — API hiba vagy rossz szimbólum "
                "(demo/éles mód egyezik a kulccsal?)."
            )
        mt5_days = len(mt5_rates)
        bin_days = len(binance_klines)
        return (
            f"MT5 ({mt5_days} nap) és Binance ({bin_days} nap) dátumai nem illeszkednek — "
            "nincs közös nap az összevonáshoz."
        )

    def _apply_chart_strategy_levels(self) -> None:
        strategy = get_strategy_config()
        self._chart.set_strategy_levels(
            strategy["base"],
            strategy["levels"],
            stop_loss=strategy.get("stop_loss"),
        )

    def _load_history_if_needed(self, force: bool = False) -> None:
        symbol = self._get_current_symbol()
        if symbol is None:
            self._chart.clear()
            return

        if not self._mt5.is_connected or not self._binance.is_connected:
            return

        if self._chart.is_history_loading:
            return

        if not force and not self._chart.needs_history_reload(symbol["mt5"], symbol["binance"]):
            return

        self._chart.mark_history_loading()
        self._log("30 napos D1 grafikon betöltése...")

        def worker() -> None:
            mt5_rates = self._mt5.get_daily_rates(symbol["mt5"], 30)
            binance_klines = self._binance.get_daily_klines(symbol["binance"], 30)
            df_diff = build_diff_dataframe(mt5_rates, binance_klines)
            history_error = self._history_load_error(
                symbol, mt5_rates, binance_klines, df_diff
            )

            def finish() -> None:
                if df_diff is None:
                    self._log(
                        "Grafikon betöltés sikertelen (nincs elegendő történelmi adat). "
                        f"{history_error}"
                    )
                    self._chart.clear()
                else:
                    self._chart.set_history(
                        df_diff,
                        symbol["mt5"],
                        symbol["binance"],
                        symbol["name"],
                    )
                    self._apply_chart_strategy_levels()
                    self._log("30 napos D1 grafikon betöltve.")

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _on_tick_snapshot(self, snapshot: TickSnapshot) -> None:
        self.after(0, lambda snap=snapshot: self._update_chart_from_snapshot(snap))

    def _update_chart_from_snapshot(self, snapshot: TickSnapshot) -> None:
        if not snapshot.is_complete:
            return

        strategy = get_strategy_config()
        self._chart.update_live(
            snapshot.diff,
            snapshot.mt5_bid,
            snapshot.binance_price,
            chart_refresh_ms=get_chart_refresh_ms(load_config()),
            tick_refresh_ms=get_price_refresh_ms(load_config()),
            tick_source=snapshot.source,
            mt5_spread=snapshot.mt5_spread,
            binance_spread=snapshot.binance_spread,
            mt5_max_spread=float(strategy["mt5_max_spread"]),
            binance_max_spread=float(strategy["binance_max_spread"]),
        )

    def _start_runtime(self) -> None:
        symbol = self._get_current_symbol()
        if symbol is None:
            return
        self._load_history_if_needed(force=True)
        self._runtime.start(symbol)
        self._runtime_active = True

    def _stop_runtime(self) -> None:
        if self._runtime_active:
            self._runtime.stop()
            self._runtime_active = False

    def _open_settings(self) -> None:
        SettingsDialog(self, on_saved=self._on_settings_saved)

    def _on_settings_saved(self) -> None:
        market = load_config().get("market_hours", {})
        timezone = market.get("timezone", "—")
        self._log(f"Beállítások frissítve. Időzóna: {timezone}")
        self._reload_symbol_selector()
        self._apply_chart_strategy_levels()
        self._refresh_status_labels()
        self._last_market_open = None
        self._check_market_status(force_log=True)
        if self._runtime_active:
            self._stop_runtime()
            self._start_runtime()
        elif self._mt5.is_connected or self._binance.is_connected:
            self._load_history_if_needed(force=True)

    def _connect(self) -> None:
        if self._busy:
            return

        config = load_config()
        self._set_busy(True)
        self._log("Csatlakozás indul...")

        def worker() -> None:
            mt5_result = self._mt5.connect(config.get("mt5", {}))
            binance_result = self._binance.connect(config.get("binance", {}))

            def finish() -> None:
                self._log(mt5_result.message)
                self._log(binance_result.message)
                self._refresh_status_labels()
                self._set_busy(False)

                if mt5_result.success or binance_result.success:
                    self._check_market_status(force_log=True)
                    self._start_runtime()
                else:
                    self._chart.clear()

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _disconnect(self) -> None:
        self._stop_runtime()
        self._mt5.disconnect()
        self._binance.disconnect()
        self._refresh_status_labels()
        self._chart.clear()
        self._log("Kapcsolatok bontva.")

    def _on_close(self) -> None:
        self._log("ForexTrader leállítva — kilépés a programból.")
        self._stop_runtime()
        self._stop_market_watch()
        self._mt5.disconnect()
        self._binance.disconnect()
        self._runtime.shutdown()
        flush_logs()
        self.destroy()

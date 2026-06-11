from __future__ import annotations

import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import messagebox, scrolledtext, ttk

from src.brokers.binance_client import BinanceFuturesClient
from src.brokers.mt5_client import MT5Client
from src.config.settings import (
    get_price_refresh_ms,
    get_selected_symbol,
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
from src.logger.app_logger import read_recent_log_lines, setup_logger
from src.trading.market_hours import format_market_schedule, is_market_open


class TradingApp(tk.Tk):
    MARKET_CHECK_SEC = 30

    def __init__(self) -> None:
        super().__init__()
        self.title("ForexTrader")
        self.minsize(780, 640)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._mt5 = MT5Client()
        self._binance = BinanceFuturesClient()
        self._busy = False
        self._price_job: str | None = None
        self._price_fetching = False
        self._symbol_var = tk.StringVar()
        self._last_market_open: bool | None = None
        self._market_job: str | None = None

        self._build_ui()
        self._init_logging()
        self._reload_symbol_selector()
        self._refresh_status_labels()
        self._check_market_status(force_log=True)
        self._start_market_watch()

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=12)
        header.pack(fill="x")

        ttk.Label(header, text="ForexTrader", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            header,
            text="MT5 és Binance Futures — különbségi D1 grafikon és számla információk.",
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
        self._account_btn = ttk.Button(
            buttons,
            text="Account Info",
            command=self._show_account_info,
        )
        self._disconnect_btn = ttk.Button(
            buttons,
            text="Disconnect",
            command=self._disconnect,
        )

        self._settings_btn.pack(side="left")
        self._connect_btn.pack(side="left", padx=(8, 0))
        self._account_btn.pack(side="left", padx=(8, 0))
        self._disconnect_btn.pack(side="left", padx=(8, 0))

        chart_frame = ttk.LabelFrame(self, text="Különbségi árfolyam (30 nap, D1)", padding=8)
        chart_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))

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
        self._chart.pack(fill="both", expand=True)

        info_frame = ttk.LabelFrame(self, text="Számla információ", padding=12)
        info_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        self._account_text = scrolledtext.ScrolledText(
            info_frame,
            height=8,
            wrap="word",
            font=("Consolas", 10),
        )
        self._account_text.pack(fill="both", expand=True)
        self._account_text.configure(state="disabled")

        log_frame = ttk.LabelFrame(self, text="Napló", padding=12)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        log_toolbar = ttk.Frame(log_frame)
        log_toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(log_toolbar, text="Előzmények", command=self._open_log_viewer).pack(side="left")
        ttk.Button(log_toolbar, text="Napló újratöltése", command=self._reload_log_history).pack(
            side="left", padx=(8, 0)
        )

        self._log_text = scrolledtext.ScrolledText(
            log_frame,
            height=5,
            wrap="word",
            font=("Consolas", 9),
        )
        self._log_text.pack(fill="both", expand=True)
        self._log_text.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self._connect_btn.configure(state=state)
        self._account_btn.configure(state=state)
        self._disconnect_btn.configure(state=state)

    def _init_logging(self) -> None:
        self._reload_log_history()
        setup_logger(gui_callback=self._append_log_line)
        self._log("ForexTrader elindult.")

    def _append_log_line(self, line: str) -> None:
        def write() -> None:
            self._log_text.configure(state="normal")
            self._log_text.insert("end", line + "\n")
            self._log_text.see("end")
            self._log_text.configure(state="disabled")

        self.after(0, write)

    def _reload_log_history(self) -> None:
        lines = read_recent_log_lines()
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        if lines:
            self._log_text.insert("1.0", "\n".join(lines) + "\n")
            self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _open_log_viewer(self) -> None:
        LogViewerDialog(self)

    def _log(self, message: str) -> None:
        app_log(message)

    def _set_account_text(self, content: str) -> None:
        self._account_text.configure(state="normal")
        self._account_text.delete("1.0", "end")
        self._account_text.insert("1.0", content)
        self._account_text.configure(state="disabled")

    def _refresh_status_labels(self) -> None:
        mt5_text = "MT5: csatlakozva" if self._mt5.is_connected else "MT5: nincs csatlakozva"
        binance_text = (
            "Binance: csatlakozva" if self._binance.is_connected else "Binance: nincs csatlakozva"
        )
        self._mt5_status.configure(text=mt5_text)
        self._binance_status.configure(text=binance_text)
        self._update_market_status_label()

    @property
    def is_trading_allowed(self) -> bool:
        return is_market_open(load_config())

    def _update_market_status_label(self) -> None:
        config = load_config()
        market_cfg = config.get("market_hours", {})
        if not market_cfg.get("enabled", True):
            self._market_status.configure(text="Kereskedés: nincs időkorlát (ellenőrzés kikapcsolva)")
            return

        if self.is_trading_allowed:
            self._market_status.configure(text="Kereskedés: engedélyezve (piac nyitva)")
        else:
            self._market_status.configure(text="Kereskedés: várakozás piacnyitásra")

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
            schedule = format_market_schedule(config)
            if is_open:
                self._log(f"Kereskedés engedélyezve — piac nyitva. ({schedule})")
            else:
                self._log(f"Várakozás piacnyitásra — a piac jelenleg zárva. ({schedule})")
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
        self._refresh_prices_now()

    def _get_current_symbol(self) -> dict[str, str] | None:
        return get_selected_symbol(load_config())

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

            def finish() -> None:
                if df_diff is None:
                    self._log("Grafikon betöltés sikertelen (nincs elegendő történelmi adat).")
                    self._chart.clear()
                else:
                    self._chart.set_history(
                        df_diff,
                        symbol["mt5"],
                        symbol["binance"],
                        symbol["name"],
                    )
                    self._log("30 napos D1 grafikon betöltve.")

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _update_live_view(
        self,
        mt5_tick: dict | None,
        binance_tick: dict | None,
    ) -> None:
        mt5_bid = mt5_tick.get("bid") if mt5_tick else None
        binance_price = binance_tick.get("price") if binance_tick else None

        current_diff = None
        if mt5_bid is not None and binance_price is not None:
            current_diff = mt5_bid - binance_price

        refresh_ms = get_price_refresh_ms(load_config())
        self._chart.update_live(current_diff, mt5_bid, binance_price, refresh_ms)

    def _start_price_refresh(self) -> None:
        self._stop_price_refresh()
        self._load_history_if_needed(force=True)
        self._refresh_prices_now()

    def _stop_price_refresh(self) -> None:
        if self._price_job is not None:
            self.after_cancel(self._price_job)
            self._price_job = None

    def _schedule_price_refresh(self) -> None:
        refresh_ms = get_price_refresh_ms(load_config())
        self._price_job = self.after(refresh_ms, self._refresh_prices_now)

    def _refresh_prices_now(self) -> None:
        symbol = self._get_current_symbol()
        if symbol is None:
            self._chart.clear()
            return

        if not self._mt5.is_connected and not self._binance.is_connected:
            return

        self._load_history_if_needed()

        if self._price_fetching:
            self._schedule_price_refresh()
            return

        self._price_fetching = True

        def worker() -> None:
            mt5_tick = None
            binance_tick = None

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = []
                if self._mt5.is_connected:
                    futures.append(("mt5", pool.submit(self._mt5.get_tick, symbol["mt5"])))
                if self._binance.is_connected:
                    futures.append(
                        ("binance", pool.submit(self._binance.get_ticker, symbol["binance"]))
                    )

                for name, future in futures:
                    try:
                        result = future.result()
                        if name == "mt5":
                            mt5_tick = result
                        else:
                            binance_tick = result
                    except Exception:
                        pass

            def finish() -> None:
                self._price_fetching = False
                self._update_live_view(mt5_tick, binance_tick)
                if self._mt5.is_connected or self._binance.is_connected:
                    self._schedule_price_refresh()

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _open_settings(self) -> None:
        SettingsDialog(self, on_saved=self._on_settings_saved)

    def _on_settings_saved(self) -> None:
        market = load_config().get("market_hours", {})
        timezone = market.get("timezone", "—")
        self._log(f"Beállítások frissítve. Időzóna: {timezone}")
        self._reload_symbol_selector()
        self._last_market_open = None
        self._check_market_status(force_log=True)
        if self._mt5.is_connected or self._binance.is_connected:
            self._stop_price_refresh()
            self._load_history_if_needed(force=True)
            self._refresh_prices_now()

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
                    self._show_account_info(show_errors=False)
                    self._check_market_status(force_log=True)
                    self._start_price_refresh()
                else:
                    self._chart.clear()

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _disconnect(self) -> None:
        self._stop_price_refresh()
        self._mt5.disconnect()
        self._binance.disconnect()
        self._refresh_status_labels()
        self._set_account_text("")
        self._chart.clear()
        self._log("Kapcsolatok bontva.")

    def _on_close(self) -> None:
        self._log("ForexTrader leállítva — kilépés a programból.")
        self._stop_price_refresh()
        self._stop_market_watch()
        self._mt5.disconnect()
        self._binance.disconnect()
        flush_logs()
        self.destroy()

    def _show_account_info(self, show_errors: bool = True) -> None:
        if not self._mt5.is_connected and not self._binance.is_connected:
            if show_errors:
                messagebox.showwarning(
                    "Nincs csatlakozás",
                    "Először csatlakozz a Connect gombbal, vagy állítsd be a Beállításokat.",
                )
            return

        lines: list[str] = []

        if self._mt5.is_connected:
            account = self._mt5.get_account_info()
            if account:
                lines.extend(
                    [
                        "=== MetaTrader 5 ===",
                        f"Login:      {account['login']}",
                        f"Név:        {account['name']}",
                        f"Szerver:    {account['server']}",
                        f"Pénznem:    {account['currency']}",
                        f"Egyenleg:   {account['balance']:.2f}",
                        f"Tőke:       {account['equity']:.2f}",
                        f"Margin:     {account['margin']:.2f}",
                        f"Szabad:     {account['free_margin']:.2f}",
                        f"Margin %:   {account['margin_level']:.2f}",
                        f"Tőkeáttét:  1:{account['leverage']}",
                        f"Profit:     {account['profit']:.2f}",
                        "",
                    ]
                )
            else:
                lines.extend(["=== MetaTrader 5 ===", "Nem sikerült lekérni a számla adatokat.", ""])

        if self._binance.is_connected:
            account = self._binance.get_account_info()
            if account:
                lines.extend(
                    [
                        "=== Binance Futures ===",
                        f"Mód:        {account['mode']}",
                        f"USDT össz:  {account['usdt_total']:.4f}",
                        f"USDT szabad:{account['usdt_free']:.4f}",
                        f"USDT haszn.:{account['usdt_used']:.4f}",
                        "",
                        "Eszközök:",
                    ]
                )
                assets = account.get("assets") or {}
                if assets:
                    for asset, values in sorted(assets.items()):
                        lines.append(
                            f"  {asset}: total={values['total']:.6f}, "
                            f"free={values['free']:.6f}, used={values['used']:.6f}"
                        )
                else:
                    lines.append("  (nincs nem nulla egyenleg)")
                lines.append("")
            else:
                lines.extend(
                    ["=== Binance Futures ===", "Nem sikerült lekérni a számla adatokat.", ""]
                )

        self._set_account_text("\n".join(lines).strip())
        self._log("Számla információ frissítve.")

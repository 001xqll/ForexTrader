from __future__ import annotations

import tkinter as tk
from copy import deepcopy
from tkinter import messagebox, ttk
from typing import Any, Callable

from src.config.settings import load_config, save_config
from src.config.settings import CONFIG_PATH
from src.trading.market_hours import format_market_time, parse_market_time, validate_timezone


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, on_saved: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.title("Csatlakozási beállítások")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._on_saved = on_saved
        self._config = load_config()

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)

        mt5_frame = ttk.Frame(notebook, padding=12)
        binance_frame = ttk.Frame(notebook, padding=12)
        symbols_frame = ttk.Frame(notebook, padding=12)
        strategy_frame = ttk.Frame(notebook, padding=12)
        market_frame = ttk.Frame(notebook, padding=12)
        notebook.add(mt5_frame, text="MetaTrader 5")
        notebook.add(binance_frame, text="Binance Futures")
        notebook.add(symbols_frame, text="Szimbólumok")
        notebook.add(strategy_frame, text="Stratégia")
        notebook.add(market_frame, text="Piaci nyitvatartás")

        self._mt5_vars = self._build_mt5_form(mt5_frame)
        self._binance_vars = self._build_binance_form(binance_frame)
        self._build_symbols_form(symbols_frame)
        self._build_strategy_form(strategy_frame)
        self._market_vars = self._build_market_hours_form(market_frame)

        button_row = ttk.Frame(self)
        button_row.pack(fill="x", padx=12, pady=(0, 12))

        ttk.Button(button_row, text="Mégse", command=self.destroy).pack(side="right")
        ttk.Button(button_row, text="Mentés", command=self._save).pack(side="right", padx=(0, 8))

        self._load_values()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build_mt5_form(self, parent: ttk.Frame) -> dict[str, Any]:
        vars_map: dict[str, Any] = {}

        fields = [
            ("login", "Login (számlaszám)", "int"),
            ("password", "Jelszó", "password"),
            ("server", "Szerver", "text"),
            ("terminal_path", "MT5 terminál útvonal (opcionális)", "text"),
        ]

        for row, (key, label, field_type) in enumerate(fields):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
            if field_type == "password":
                var = tk.StringVar()
                widget = ttk.Entry(parent, textvariable=var, width=42, show="*")
            elif field_type == "int":
                var = tk.StringVar()
                widget = ttk.Entry(parent, textvariable=var, width=42)
            else:
                var = tk.StringVar()
                widget = ttk.Entry(parent, textvariable=var, width=42)

            widget.grid(row=row, column=1, sticky="ew", pady=4)
            vars_map[key] = var

        parent.columnconfigure(1, weight=1)
        ttk.Label(
            parent,
            text="Tipp: az MT5 terminálnak futnia kell Windows alatt.",
            foreground="#666666",
        ).grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(12, 0))

        return vars_map

    def _build_binance_form(self, parent: ttk.Frame) -> dict[str, Any]:
        vars_map: dict[str, Any] = {}

        fields = [
            ("api_key", "API Key", False),
            ("api_secret", "API Secret", True),
        ]

        for row, (key, label, secret) in enumerate(fields):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
            var = tk.StringVar()
            widget = ttk.Entry(parent, textvariable=var, width=42, show="*" if secret else "")
            widget.grid(row=row, column=1, sticky="ew", pady=4)
            vars_map[key] = var

        demo_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            parent,
            text="Demo / testnet (ugyanaz, mint ProTrader-ben: testnet=true)",
            variable=demo_var,
        ).grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(8, 0))
        vars_map["demo"] = demo_var

        ws_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            parent,
            text="Websocket árfolyam (bookTicker, ajánlott — gyorsabb mint REST)",
            variable=ws_var,
        ).grid(row=len(fields) + 1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        vars_map["use_websocket"] = ws_var

        parent.columnconfigure(1, weight=1)
        ttk.Label(
            parent,
            text="Tipp: ugyanazt a demo API kulcsot add meg, mint a ProTrader config.yaml-ban.",
            foreground="#666666",
        ).grid(row=len(fields) + 2, column=0, columnspan=2, sticky="w", pady=(12, 0))

        return vars_map

    def _build_symbols_form(self, parent: ttk.Frame) -> None:
        symbols_cfg = self._config.get("symbols", {})
        self._symbol_pairs: list[dict[str, str]] = [
            {
                "name": str(pair.get("name", "")).strip(),
                "mt5": str(pair.get("mt5", "")).strip(),
                "binance": str(pair.get("binance", "")).strip().upper(),
            }
            for pair in symbols_cfg.get("pairs", [])
            if pair.get("mt5") and pair.get("binance")
        ]
        self._selected_pair_index = int(symbols_cfg.get("selected_index") or 0)

        ttk.Label(
            parent,
            text="MT5 és Binance Futures párok (pl. GOLD# ↔ PAXGUSDT).",
            foreground="#555555",
        ).pack(anchor="w", pady=(0, 8))

        list_frame = ttk.Frame(parent)
        list_frame.pack(fill="both", expand=True)

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")

        self._pairs_listbox = tk.Listbox(
            list_frame,
            height=8,
            yscrollcommand=scrollbar.set,
            exportselection=False,
        )
        self._pairs_listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self._pairs_listbox.yview)
        self._pairs_listbox.bind("<<ListboxSelect>>", self._on_pair_selected)

        form = ttk.Frame(parent)
        form.pack(fill="x", pady=(12, 0))
        form.columnconfigure(1, weight=1)

        self._sym_name = tk.StringVar()
        self._sym_mt5 = tk.StringVar()
        self._sym_binance = tk.StringVar()

        fields = [
            ("name", "Név (megjelenítés)", self._sym_name),
            ("mt5", "MT5 szimbólum", self._sym_mt5),
            ("binance", "Binance Futures", self._sym_binance),
        ]
        for row, (_, label, var) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(form, textvariable=var, width=40).grid(row=row, column=1, sticky="ew", pady=4)

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Hozzáadás", command=self._add_symbol_pair).pack(side="left")
        ttk.Button(buttons, text="Frissítés", command=self._update_symbol_pair).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(buttons, text="Törlés", command=self._delete_symbol_pair).pack(
            side="left", padx=(8, 0)
        )

        refresh_row = ttk.Frame(parent)
        refresh_row.pack(fill="x", pady=(16, 0))
        ttk.Label(refresh_row, text="Tick frissítés (ms):").pack(side="left")
        self._refresh_ms = tk.StringVar(value="300")
        ttk.Entry(refresh_row, textvariable=self._refresh_ms, width=8).pack(side="left", padx=(8, 0))

        chart_row = ttk.Frame(parent)
        chart_row.pack(fill="x", pady=(8, 0))
        ttk.Label(chart_row, text="Grafikon frissítés (ms):").pack(side="left")
        self._chart_refresh_ms = tk.StringVar(value="1000")
        ttk.Entry(chart_row, textvariable=self._chart_refresh_ms, width=8).pack(side="left", padx=(8, 0))

        ttk.Label(
            parent,
            text="Tick: kereskedési motor (100–5000 ms). Grafikon: csak megjelenítés (200–5000 ms, alap 1000).",
            foreground="#666666",
        ).pack(anchor="w", pady=(6, 0))

        self._refresh_pairs_listbox()

    def _build_strategy_form(self, parent: ttk.Frame) -> None:
        ttk.Label(
            parent,
            text=(
                "Kék = bázis, zöld = bázis fölötti szintek, sárga = bázis alatti szintek, "
                "piros = stop-loss (bázis ±). Zárás: diff a bázis közelébe (± zárási küszöb)."
            ),
            foreground="#555555",
        ).pack(anchor="w", pady=(0, 10))

        form = ttk.Frame(parent)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        self._strat_base = tk.StringVar(value="10.0")
        self._strat_exit_threshold = tk.StringVar(value="1.0")
        self._strat_stop_loss = tk.StringVar(value="15.0")
        self._strat_lot = tk.StringVar(value="0.01")
        self._strat_binance_qty = tk.StringVar(value="")
        self._strat_mt5_max_spread = tk.StringVar(value="100")
        self._strat_binance_max_spread = tk.StringVar(value="100")
        self._strat_dry_run = tk.BooleanVar(value=True)

        ttk.Label(form, text="Bázis (kék vonal)").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(form, textvariable=self._strat_base, width=12).grid(row=0, column=1, sticky="w", pady=4)
        ttk.Label(form, text="Zárási küszöb (bázishoz)").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(form, textvariable=self._strat_exit_threshold, width=12).grid(
            row=1, column=1, sticky="w", pady=4
        )
        ttk.Label(form, text="Stop-loss (bázistól ±)").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(form, textvariable=self._strat_stop_loss, width=12).grid(
            row=2, column=1, sticky="w", pady=4
        )
        ttk.Label(form, text="MT5 lot").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(form, textvariable=self._strat_lot, width=12).grid(row=3, column=1, sticky="w", pady=4)
        ttk.Label(form, text="Binance qty (üres = lot×100)").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Entry(form, textvariable=self._strat_binance_qty, width=12).grid(
            row=4, column=1, sticky="w", pady=4
        )
        ttk.Label(form, text="Max spread MT5 (point)").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Entry(form, textvariable=self._strat_mt5_max_spread, width=12).grid(
            row=5, column=1, sticky="w", pady=4
        )
        ttk.Label(form, text="Max spread Binance (USD)").grid(row=6, column=0, sticky="w", pady=4)
        ttk.Entry(form, textvariable=self._strat_binance_max_spread, width=12).grid(
            row=6, column=1, sticky="w", pady=4
        )
        ttk.Checkbutton(
            form,
            text="Dry-run (ne küldjön éles megbízást)",
            variable=self._strat_dry_run,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Label(parent, text="Szintek távolsága a bázistól:").pack(anchor="w", pady=(12, 4))
        self._strategy_levels: list[float] = []
        self._selected_level_index = 0

        list_frame = ttk.Frame(parent)
        list_frame.pack(fill="x")
        self._levels_listbox = tk.Listbox(list_frame, height=5, exportselection=False)
        self._levels_listbox.pack(fill="x")
        self._levels_listbox.bind("<<ListboxSelect>>", self._on_level_selected)

        level_form = ttk.Frame(parent)
        level_form.pack(fill="x", pady=(8, 0))
        self._level_value = tk.StringVar()
        ttk.Label(level_form, text="Szint érték:").pack(side="left")
        ttk.Entry(level_form, textvariable=self._level_value, width=10).pack(side="left", padx=(8, 0))

        level_buttons = ttk.Frame(parent)
        level_buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(level_buttons, text="Hozzáadás", command=self._add_level).pack(side="left")
        ttk.Button(level_buttons, text="Frissítés", command=self._update_level).pack(side="left", padx=(8, 0))
        ttk.Button(level_buttons, text="Törlés", command=self._delete_level).pack(side="left", padx=(8, 0))

    def _refresh_levels_listbox(self) -> None:
        self._levels_listbox.delete(0, tk.END)
        for index, level in enumerate(self._strategy_levels):
            self._levels_listbox.insert(tk.END, f"+/- {level:g}  (bázis ± {level:g})")
            if index == self._selected_level_index:
                self._levels_listbox.selection_set(index)
                self._levels_listbox.activate(index)
        if self._strategy_levels and self._selected_level_index < len(self._strategy_levels):
            self._level_value.set(str(self._strategy_levels[self._selected_level_index]))
        else:
            self._level_value.set("")

    def _on_level_selected(self, _event: tk.Event | None = None) -> None:
        selection = self._levels_listbox.curselection()
        if not selection:
            return
        self._selected_level_index = selection[0]
        self._level_value.set(str(self._strategy_levels[self._selected_level_index]))

    def _read_level_value(self) -> float | None:
        raw = self._level_value.get().strip().replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            messagebox.showerror("Hiba", "A szint csak szám lehet.")
            return None
        if value <= 0:
            messagebox.showerror("Hiba", "A szintnek pozitívnak kell lennie.")
            return None
        return value

    def _add_level(self) -> None:
        value = self._read_level_value()
        if value is None:
            return
        self._strategy_levels.append(value)
        self._strategy_levels.sort()
        self._selected_level_index = self._strategy_levels.index(value)
        self._refresh_levels_listbox()

    def _update_level(self) -> None:
        value = self._read_level_value()
        if value is None or not self._strategy_levels:
            return
        if self._selected_level_index < 0 or self._selected_level_index >= len(self._strategy_levels):
            messagebox.showerror("Hiba", "Válassz ki egy szintet.")
            return
        self._strategy_levels[self._selected_level_index] = value
        self._strategy_levels.sort()
        self._selected_level_index = self._strategy_levels.index(value)
        self._refresh_levels_listbox()

    def _delete_level(self) -> None:
        if not self._strategy_levels:
            return
        if self._selected_level_index < 0 or self._selected_level_index >= len(self._strategy_levels):
            messagebox.showerror("Hiba", "Válassz ki egy szintet.")
            return
        del self._strategy_levels[self._selected_level_index]
        self._selected_level_index = max(0, min(self._selected_level_index, len(self._strategy_levels) - 1))
        self._refresh_levels_listbox()

    def _build_market_hours_form(self, parent: ttk.Frame) -> dict[str, Any]:
        vars_map: dict[str, Any] = {}

        enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            parent,
            text="Piaci nyitvatartás ellenőrzése (7/24 futáshoz)",
            variable=enabled_var,
        ).pack(anchor="w")
        vars_map["enabled"] = enabled_var

        form = ttk.Frame(parent)
        form.pack(fill="x", pady=(12, 0))
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Időzóna").grid(row=0, column=0, sticky="w", pady=4)
        timezone_var = tk.StringVar(value="Europe/Budapest")
        timezone_combo = ttk.Combobox(
            form,
            textvariable=timezone_var,
            values=[
                "Europe/Budapest",
                "Europe/Kiev",
                "Europe/London",
                "Europe/Berlin",
                "UTC",
                "America/New_York",
            ],
            width=38,
            state="readonly",
        )
        timezone_combo.grid(row=0, column=1, sticky="ew", pady=4)
        vars_map["timezone"] = timezone_var
        vars_map["timezone_widget"] = timezone_combo

        ttk.Label(form, text="Nyitás (HH:MM:SS)").grid(row=1, column=0, sticky="w", pady=4)
        open_var = tk.StringVar(value="01:02:00")
        ttk.Entry(form, textvariable=open_var, width=12).grid(row=1, column=1, sticky="w", pady=4)
        vars_map["open_time"] = open_var

        ttk.Label(form, text="Zárás (HH:MM:SS)").grid(row=2, column=0, sticky="w", pady=4)
        close_var = tk.StringVar(value="23:58:00")
        ttk.Entry(form, textvariable=close_var, width=12).grid(row=2, column=1, sticky="w", pady=4)
        vars_map["close_time"] = close_var

        days_frame = ttk.LabelFrame(parent, text="Kereskedési napok", padding=8)
        days_frame.pack(fill="x", pady=(12, 0))

        day_defs = [
            ("Hétfő", 0),
            ("Kedd", 1),
            ("Szerda", 2),
            ("Csütörtök", 3),
            ("Péntek", 4),
            ("Szombat", 5),
            ("Vasárnap", 6),
        ]
        vars_map["trading_days"] = {}
        for index, (label, day_id) in enumerate(day_defs):
            day_var = tk.BooleanVar(value=day_id < 5)
            ttk.Checkbutton(days_frame, text=label, variable=day_var).grid(
                row=index // 4,
                column=index % 4,
                sticky="w",
                padx=(0, 12),
                pady=2,
            )
            vars_map["trading_days"][day_id] = day_var

        ttk.Label(
            parent,
            text="Zárva tartás idején a program fut, de kereskedés nem engedélyezett.",
            foreground="#666666",
        ).pack(anchor="w", pady=(12, 0))

        return vars_map

    def _refresh_pairs_listbox(self) -> None:
        self._pairs_listbox.delete(0, tk.END)
        for index, pair in enumerate(self._symbol_pairs):
            label = f"{pair['name']}  |  {pair['mt5']}  |  {pair['binance']}"
            self._pairs_listbox.insert(tk.END, label)
            if index == self._selected_pair_index:
                self._pairs_listbox.selection_set(index)
                self._pairs_listbox.activate(index)

        if self._symbol_pairs and self._selected_pair_index < len(self._symbol_pairs):
            self._fill_symbol_form(self._symbol_pairs[self._selected_pair_index])
        else:
            self._sym_name.set("")
            self._sym_mt5.set("")
            self._sym_binance.set("")

    def _fill_symbol_form(self, pair: dict[str, str]) -> None:
        self._sym_name.set(pair.get("name", ""))
        self._sym_mt5.set(pair.get("mt5", ""))
        self._sym_binance.set(pair.get("binance", ""))

    def _on_pair_selected(self, _event: tk.Event | None = None) -> None:
        selection = self._pairs_listbox.curselection()
        if not selection:
            return
        self._selected_pair_index = selection[0]
        self._fill_symbol_form(self._symbol_pairs[self._selected_pair_index])

    def _read_symbol_form(self) -> dict[str, str] | None:
        name = self._sym_name.get().strip()
        mt5 = self._sym_mt5.get().strip()
        binance = self._sym_binance.get().strip().upper()

        if not mt5 or not binance:
            messagebox.showerror("Hiba", "Az MT5 és Binance szimbólum megadása kötelező.")
            return None

        if not name:
            name = f"{mt5} / {binance}"

        return {"name": name, "mt5": mt5, "binance": binance}

    def _add_symbol_pair(self) -> None:
        pair = self._read_symbol_form()
        if pair is None:
            return

        self._symbol_pairs.append(pair)
        self._selected_pair_index = len(self._symbol_pairs) - 1
        self._refresh_pairs_listbox()

    def _update_symbol_pair(self) -> None:
        if not self._symbol_pairs:
            self._add_symbol_pair()
            return

        pair = self._read_symbol_form()
        if pair is None:
            return

        index = self._selected_pair_index
        if index < 0 or index >= len(self._symbol_pairs):
            messagebox.showerror("Hiba", "Válassz ki egy szimbólum párt a listából.")
            return

        self._symbol_pairs[index] = pair
        self._refresh_pairs_listbox()

    def _delete_symbol_pair(self) -> None:
        if not self._symbol_pairs:
            return

        index = self._selected_pair_index
        if index < 0 or index >= len(self._symbol_pairs):
            messagebox.showerror("Hiba", "Válassz ki egy szimbólum párt a törléshez.")
            return

        del self._symbol_pairs[index]
        self._selected_pair_index = max(0, min(index, len(self._symbol_pairs) - 1))
        self._refresh_pairs_listbox()

    def _load_values(self) -> None:
        mt5 = self._config.get("mt5", {})
        self._mt5_vars["login"].set(str(mt5.get("login") or ""))
        self._mt5_vars["password"].set(mt5.get("password", ""))
        self._mt5_vars["server"].set(mt5.get("server", ""))
        self._mt5_vars["terminal_path"].set(mt5.get("terminal_path", ""))

        binance = self._config.get("binance", {})
        self._binance_vars["api_key"].set(binance.get("api_key", ""))
        self._binance_vars["api_secret"].set(binance.get("api_secret", ""))
        self._binance_vars["demo"].set(bool(binance.get("demo", binance.get("testnet", True))))
        self._binance_vars["use_websocket"].set(bool(binance.get("use_websocket", True)))

        ui = self._config.get("ui", {})
        self._refresh_ms.set(str(ui.get("price_refresh_ms", 300)))
        self._chart_refresh_ms.set(str(ui.get("chart_refresh_ms", 1000)))

        market = self._config.get("market_hours", {})
        self._market_vars["enabled"].set(bool(market.get("enabled", True)))
        self._market_vars["timezone"].set(market.get("timezone", "Europe/Budapest"))
        self._market_vars["open_time"].set(market.get("open_time", "01:02:00"))
        self._market_vars["close_time"].set(market.get("close_time", "23:58:00"))
        trading_days = set(market.get("trading_days", [0, 1, 2, 3, 4]))
        for day_id, day_var in self._market_vars["trading_days"].items():
            day_var.set(day_id in trading_days)

        strategy = self._config.get("strategy", {})
        self._strat_base.set(str(strategy.get("base", 10.0)))
        self._strat_exit_threshold.set(str(strategy.get("exit_threshold", 1.0)))
        self._strat_stop_loss.set(str(strategy.get("stop_loss", 15.0)))
        self._strat_lot.set(str(strategy.get("lot_mt5", 0.01)))
        qty = strategy.get("binance_quantity") or 0
        self._strat_binance_qty.set("" if float(qty or 0) <= 0 else str(qty))
        self._strat_dry_run.set(bool(strategy.get("dry_run", True)))
        self._strat_mt5_max_spread.set(str(strategy.get("mt5_max_spread", 100.0)))
        self._strat_binance_max_spread.set(str(strategy.get("binance_max_spread", 100.0)))
        self._strategy_levels = [float(level) for level in strategy.get("levels", [5.0, 10.0])]
        self._strategy_levels.sort()
        self._selected_level_index = 0
        self._refresh_levels_listbox()

    def _save(self) -> None:
        login_raw = self._mt5_vars["login"].get().strip()
        try:
            login = int(login_raw) if login_raw else 0
        except ValueError:
            messagebox.showerror("Hiba", "Az MT5 login csak szám lehet.")
            return

        updated = deepcopy(self._config)
        updated["mt5"] = {
            "login": login,
            "password": self._mt5_vars["password"].get(),
            "server": self._mt5_vars["server"].get().strip(),
            "terminal_path": self._mt5_vars["terminal_path"].get().strip(),
        }
        updated["binance"] = {
            "api_key": self._binance_vars["api_key"].get().strip(),
            "api_secret": self._binance_vars["api_secret"].get().strip(),
            "demo": bool(self._binance_vars["demo"].get()),
            "use_websocket": bool(self._binance_vars["use_websocket"].get()),
        }
        updated["symbols"] = {
            "pairs": self._symbol_pairs,
            "selected_index": max(0, min(self._selected_pair_index, len(self._symbol_pairs) - 1))
            if self._symbol_pairs
            else 0,
        }

        try:
            refresh_ms = int(self._refresh_ms.get().strip())
            chart_refresh_ms = int(self._chart_refresh_ms.get().strip())
        except ValueError:
            messagebox.showerror("Hiba", "A frissítési idők csak számok lehetnek (ms).")
            return
        if refresh_ms < 100 or refresh_ms > 5000:
            messagebox.showerror("Hiba", "A tick frissítés 100 és 5000 ms között lehet.")
            return
        if chart_refresh_ms < 200 or chart_refresh_ms > 5000:
            messagebox.showerror("Hiba", "A grafikon frissítés 200 és 5000 ms között lehet.")
            return
        updated["ui"] = {
            "price_refresh_ms": refresh_ms,
            "chart_refresh_ms": chart_refresh_ms,
        }

        try:
            base = float(self._strat_base.get().strip().replace(",", "."))
            exit_threshold = float(self._strat_exit_threshold.get().strip().replace(",", "."))
            stop_loss = float(self._strat_stop_loss.get().strip().replace(",", "."))
            lot_mt5 = float(self._strat_lot.get().strip().replace(",", "."))
            qty_raw = self._strat_binance_qty.get().strip().replace(",", ".")
            binance_qty = float(qty_raw) if qty_raw else 0.0
            mt5_max_spread = float(self._strat_mt5_max_spread.get().strip().replace(",", "."))
            binance_max_spread = float(
                self._strat_binance_max_spread.get().strip().replace(",", ".")
            )
        except ValueError:
            messagebox.showerror("Hiba", "A stratégia numerikus mezői csak számok lehetnek.")
            return
        if exit_threshold < 0:
            messagebox.showerror("Hiba", "A zárási küszöb nem lehet negatív.")
            return
        if stop_loss < 0:
            messagebox.showerror("Hiba", "A stop-loss nem lehet negatív.")
            return
        if lot_mt5 <= 0:
            messagebox.showerror("Hiba", "Az MT5 lot pozitív kell legyen.")
            return
        if mt5_max_spread < 0 or binance_max_spread < 0:
            messagebox.showerror("Hiba", "A max spread nem lehet negatív.")
            return
        if not self._strategy_levels:
            messagebox.showerror("Hiba", "Legalább egy szintet adj meg.")
            return

        updated["strategy"] = {
            "base": base,
            "levels": sorted(self._strategy_levels),
            "exit_threshold": exit_threshold,
            "stop_loss": stop_loss,
            "lot_mt5": lot_mt5,
            "binance_quantity": binance_qty,
            "mt5_max_spread": mt5_max_spread,
            "binance_max_spread": binance_max_spread,
            "dry_run": bool(self._strat_dry_run.get()),
        }

        try:
            timezone = validate_timezone(self._market_vars["timezone_widget"].get())
            open_time = parse_market_time(self._market_vars["open_time"].get())
            close_time = parse_market_time(self._market_vars["close_time"].get())
        except ValueError as exc:
            messagebox.showerror("Hiba", str(exc))
            return

        trading_days = [
            day_id
            for day_id, day_var in self._market_vars["trading_days"].items()
            if day_var.get()
        ]
        if self._market_vars["enabled"].get() and not trading_days:
            messagebox.showerror("Hiba", "Legalább egy kereskedési napot válassz ki.")
            return

        updated["market_hours"] = {
            "enabled": bool(self._market_vars["enabled"].get()),
            "timezone": timezone,
            "open_time": format_market_time(open_time),
            "close_time": format_market_time(close_time),
            "trading_days": sorted(trading_days),
        }

        save_config(updated)
        self._config = updated

        if self._on_saved:
            self._on_saved()

        messagebox.showinfo(
            "Mentve",
            f"A beállítások elmentve.\n\nFájl: {CONFIG_PATH}",
        )
        self.destroy()

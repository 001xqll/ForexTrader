from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle


class DiffChartPanel(ttk.Frame):
    HIST_REFRESH_SEC = 3600

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)

        self._fig = Figure(figsize=(7.5, 3.8), dpi=100, layout="tight")
        self._ax = self._fig.add_subplot(111)
        self._canvas = FigureCanvasTkAgg(self._fig, master=self)
        self._canvas.get_tk_widget().pack(fill="both", expand=True)

        self._df_cache: pd.DataFrame | None = None
        self._symbol_key: tuple[str, str] | None = None
        self._last_hist_refresh = 0.0
        self._last_live_draw = 0.0
        self._live_line = None
        self._live_label = None
        self._hist_loading = False
        self._title_symbol = ""

        self.clear()

    def clear(self) -> None:
        self._df_cache = None
        self._symbol_key = None
        self._last_hist_refresh = 0.0
        self._live_line = None
        self._live_label = None
        self._hist_loading = False
        self._ax.clear()
        self._ax.set_title("Különbségi árfolyam (MT5 − Binance)")
        self._ax.text(
            0.5,
            0.5,
            "Csatlakozz mindkét platformhoz a grafikonhoz.",
            ha="center",
            va="center",
            transform=self._ax.transAxes,
            color="#666666",
        )
        self._canvas.draw_idle()

    def needs_history_reload(self, mt5_symbol: str, binance_symbol: str) -> bool:
        key = (mt5_symbol, binance_symbol)
        if self._df_cache is None or self._symbol_key != key:
            return True
        return time.time() - self._last_hist_refresh >= self.HIST_REFRESH_SEC

    def set_history(
        self,
        df_diff: pd.DataFrame | None,
        mt5_symbol: str,
        binance_symbol: str,
        display_name: str,
    ) -> None:
        self._hist_loading = False
        self._title_symbol = display_name
        self._symbol_key = (mt5_symbol, binance_symbol)

        if df_diff is None or df_diff.empty:
            self.clear()
            return

        self._df_cache = df_diff
        self._last_hist_refresh = time.time()
        self._draw_static(df_diff)

    def update_live(
        self,
        current_diff: float | None,
        mt5_bid: float | None,
        binance_price: float | None,
        chart_refresh_ms: int,
        tick_refresh_ms: int | None = None,
        tick_source: str | None = None,
    ) -> None:
        if self._df_cache is None or current_diff is None:
            return

        min_interval_sec = max(0.2, chart_refresh_ms / 1000.0)
        now = time.time()
        if now - self._last_live_draw < min_interval_sec:
            return
        self._last_live_draw = now

        if self._live_line is not None:
            self._live_line.set_ydata([current_diff, current_diff])
        if self._live_label is not None:
            self._live_label.set_position((1.01, current_diff))
            self._live_label.set_text(f"{current_diff:+.2f}")

        mt5_text = f"{mt5_bid:.2f}" if mt5_bid is not None else "—"
        bin_text = f"{binance_price:.2f}" if binance_price is not None else "—"
        tick_ms = tick_refresh_ms if tick_refresh_ms is not None else chart_refresh_ms
        if tick_source == "binance_ws":
            binance_mode = "WS"
        elif tick_source == "rest_poll":
            binance_mode = "REST"
        else:
            binance_mode = "—"
        self._ax.set_title(
            f"{self._title_symbol}  ·  D1 különbség (30 nap)\n"
            f"Élő: {current_diff:+.2f}   MT5: {mt5_text}   Binance: {bin_text} ({binance_mode})   "
            f"(tick ~{tick_ms} ms, grafikon ~{chart_refresh_ms} ms)",
            fontsize=10,
        )
        self._canvas.draw_idle()

    def mark_history_loading(self) -> None:
        self._hist_loading = True

    @property
    def is_history_loading(self) -> bool:
        return self._hist_loading

    def _draw_candles(self, df_diff: pd.DataFrame) -> None:
        width = 0.65
        for index, row in enumerate(df_diff.itertuples()):
            open_price = float(row.Open)
            high = float(row.High)
            low = float(row.Low)
            close = float(row.Close)
            color = "#26a69a" if close >= open_price else "#ef5350"

            self._ax.plot([index, index], [low, high], color=color, linewidth=1.0, solid_capstyle="round")
            body_bottom = min(open_price, close)
            body_height = abs(close - open_price) or (high - low) * 0.02 or 0.01
            self._ax.add_patch(
                Rectangle(
                    (index - width / 2, body_bottom),
                    width,
                    body_height,
                    facecolor=color,
                    edgecolor=color,
                )
            )

        tick_step = max(1, len(df_diff) // 6)
        tick_positions = list(range(0, len(df_diff), tick_step))
        self._ax.set_xlim(-0.8, len(df_diff) - 0.2)
        self._ax.set_xticks(tick_positions)
        self._ax.set_xticklabels([df_diff.index[pos].strftime("%m-%d") for pos in tick_positions])
        self._ax.set_ylabel("MT5 − Binance")
        self._ax.grid(True, linestyle=":", alpha=0.35)

    def _draw_static(self, df_diff: pd.DataFrame) -> None:
        self._ax.clear()
        self._draw_candles(df_diff)
        self._ax.axhline(0, color="#888888", linestyle=":", alpha=0.6)

        current_diff = float(df_diff["Close"].iloc[-1])
        self._live_line = self._ax.axhline(
            current_diff,
            color="#c0392b",
            linestyle="--",
            linewidth=1.4,
        )
        self._live_label = self._ax.text(
            1.01,
            current_diff,
            f"{current_diff:+.2f}",
            transform=self._ax.get_yaxis_transform(),
            color="#c0392b",
            fontweight="bold",
            fontsize=9,
            bbox=dict(facecolor="white", alpha=0.75, edgecolor="#c0392b"),
        )
        self._canvas.draw_idle()

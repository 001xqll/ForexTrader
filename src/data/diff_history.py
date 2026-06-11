from __future__ import annotations

from typing import Any

import pandas as pd


def build_diff_dataframe(
    mt5_rates: list[dict[str, Any]] | None,
    binance_klines: list[list[Any]] | None,
) -> pd.DataFrame | None:
    if not mt5_rates or not binance_klines:
        return None

    df_mt5 = pd.DataFrame(mt5_rates)
    df_mt5["time"] = pd.to_datetime(df_mt5["time"], unit="s")
    df_mt5.set_index("time", inplace=True)
    df_mt5 = df_mt5[["open", "high", "low", "close"]]
    df_mt5.columns = ["Open", "High", "Low", "Close"]

    df_bin = pd.DataFrame(
        binance_klines,
        columns=[
            "time",
            "Open",
            "High",
            "Low",
            "Close",
            "V",
            "CT",
            "QV",
            "NT",
            "TB",
            "TQ",
            "I",
        ],
    )
    df_bin["time"] = pd.to_datetime(df_bin["time"], unit="ms")
    df_bin.set_index("time", inplace=True)
    df_bin = df_bin[["Open", "High", "Low", "Close"]].apply(pd.to_numeric)

    df_diff = df_mt5.sub(df_bin).dropna()
    if df_diff.empty:
        return None

    df_diff["Volume"] = 1
    return df_diff.tail(30)


def patch_last_candle(df_diff: pd.DataFrame, current_diff: float) -> pd.DataFrame:
    df = df_diff.copy()
    last = df.index[-1]
    df.at[last, "Close"] = current_diff
    df.at[last, "High"] = max(float(df.at[last, "High"]), current_diff)
    df.at[last, "Low"] = min(float(df.at[last, "Low"]), current_diff)
    return df

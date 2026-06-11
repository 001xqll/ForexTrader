from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "mt5": {
        "login": 0,
        "password": "",
        "server": "",
        "terminal_path": "",
    },
    "binance": {
        "api_key": "",
        "api_secret": "",
        "demo": True,
        "use_websocket": True,
    },
    "symbols": {
        "pairs": [
            {"name": "Gold", "mt5": "GOLD#", "binance": "PAXGUSDT"},
        ],
        "selected_index": 0,
    },
    "strategy": {
        "base": 10.0,
        "levels": [5.0, 10.0],
        "exit_threshold": 1.0,
        "lot_mt5": 0.01,
        "binance_quantity": 0.0,
        "dry_run": True,
    },
    "ui": {
        "price_refresh_ms": 300,
        "chart_refresh_ms": 1000,
    },
    "market_hours": {
        "enabled": True,
        "timezone": "Europe/Budapest",
        "open_time": "01:02:00",
        "close_time": "23:58:00",
        "trading_days": [0, 1, 2, 3, 4],
    },
}


def get_price_refresh_ms(config: dict[str, Any] | None = None) -> int:
    cfg = config or load_config()
    value = int(cfg.get("ui", {}).get("price_refresh_ms", 300))
    return max(100, min(value, 5000))


def get_chart_refresh_ms(config: dict[str, Any] | None = None) -> int:
    cfg = config or load_config()
    value = int(cfg.get("ui", {}).get("chart_refresh_ms", 1000))
    return max(200, min(value, 5000))


def get_strategy_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    defaults = deepcopy(DEFAULT_CONFIG["strategy"])
    merged = {**defaults, **(cfg.get("strategy") or {})}
    levels = merged.get("levels") or []
    merged["levels"] = sorted({float(level) for level in levels if float(level) > 0})
    merged["base"] = float(merged.get("base") or 0.0)
    merged["exit_threshold"] = max(0.0, float(merged.get("exit_threshold") or 1.0))
    merged["lot_mt5"] = float(merged.get("lot_mt5") or 0.01)
    qty = float(merged.get("binance_quantity") or 0.0)
    if qty <= 0:
        qty = round(merged["lot_mt5"] * 100, 3)
    merged["binance_quantity"] = qty
    merged["dry_run"] = bool(merged.get("dry_run", True))
    return merged


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return deepcopy(DEFAULT_CONFIG)

    with CONFIG_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)

    merged = deepcopy(DEFAULT_CONFIG)
    for section, values in data.items():
        if section not in merged:
            merged[section] = values
            continue
        if not isinstance(values, dict):
            continue
        if section == "symbols":
            merged["symbols"].update(values)
            if isinstance(values.get("pairs"), list) and values["pairs"]:
                merged["symbols"]["pairs"] = values["pairs"]
        elif section == "strategy":
            merged["strategy"].update(values)
            if isinstance(values.get("levels"), list):
                merged["strategy"]["levels"] = values["levels"]
        else:
            merged[section].update(values)
    return merged


def get_symbol_pairs(config: dict[str, Any] | None = None) -> list[dict[str, str]]:
    cfg = config or load_config()
    pairs = cfg.get("symbols", {}).get("pairs") or []
    return [
        {
            "name": str(pair.get("name", "")).strip(),
            "mt5": str(pair.get("mt5", "")).strip(),
            "binance": str(pair.get("binance", "")).strip().upper(),
        }
        for pair in pairs
        if pair.get("mt5") and pair.get("binance")
    ]


def get_selected_symbol(config: dict[str, Any] | None = None) -> dict[str, str] | None:
    cfg = config or load_config()
    pairs = get_symbol_pairs(cfg)
    if not pairs:
        return None
    index = int(cfg.get("symbols", {}).get("selected_index") or 0)
    index = max(0, min(index, len(pairs) - 1))
    return pairs[index]


def save_config(config: dict[str, Any]) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)

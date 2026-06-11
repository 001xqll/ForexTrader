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
    },
    "symbols": {
        "pairs": [
            {"name": "Gold", "mt5": "GOLD#", "binance": "PAXGUSDT"},
        ],
        "selected_index": 0,
    },
    "ui": {
        "price_refresh_ms": 300,
    },
}


def get_price_refresh_ms(config: dict[str, Any] | None = None) -> int:
    cfg = config or load_config()
    value = int(cfg.get("ui", {}).get("price_refresh_ms", 300))
    return max(100, min(value, 5000))

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return deepcopy(DEFAULT_CONFIG)

    with CONFIG_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)

    merged = deepcopy(DEFAULT_CONFIG)
    for section, values in data.items():
        if section not in merged or not isinstance(values, dict):
            continue
        if section == "symbols":
            merged["symbols"].update(values)
            if isinstance(values.get("pairs"), list) and values["pairs"]:
                merged["symbols"]["pairs"] = values["pairs"]
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

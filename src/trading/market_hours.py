from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

WEEKDAY_LABELS = ["Hétfő", "Kedd", "Szerda", "Csütörtök", "Péntek", "Szombat", "Vasárnap"]


def validate_timezone(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError("Az időzóna megadása kötelező.")
    try:
        ZoneInfo(name)
    except Exception as exc:
        raise ValueError(f"Érvénytelen időzóna: {name}") from exc
    return name


def parse_market_time(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) == 2:
        hour, minute, second = int(parts[0]), int(parts[1]), 0
    elif len(parts) == 3:
        hour, minute, second = int(parts[0]), int(parts[1]), int(parts[2])
    else:
        raise ValueError("Az időformátum HH:MM vagy HH:MM:SS legyen.")

    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise ValueError("Érvénytelen óra, perc vagy másodperc.")
    return time(hour=hour, minute=minute, second=second)


def format_market_time(value: time) -> str:
    if value.second:
        return value.strftime("%H:%M:%S")
    return value.strftime("%H:%M")


def parse_hhmm(value: str) -> time:
    return parse_market_time(value)


def get_market_hours_config(config: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "enabled": True,
        "timezone": "Europe/Budapest",
        "open_time": "01:02",
        "close_time": "23:58",
        "trading_days": [0, 1, 2, 3, 4],
    }
    merged = {**defaults, **(config.get("market_hours") or {})}
    days = merged.get("trading_days") or defaults["trading_days"]
    merged["trading_days"] = sorted({int(day) for day in days if 0 <= int(day) <= 6})
    return merged


def is_market_open(config: dict[str, Any] | None = None) -> bool:
    from src.config.settings import load_config

    cfg = get_market_hours_config(config or load_config())
    if not cfg.get("enabled", True):
        return True

    try:
        tz = ZoneInfo(str(cfg.get("timezone", "Europe/Budapest")))
        open_time = parse_market_time(str(cfg.get("open_time", "00:00")))
        close_time = parse_market_time(str(cfg.get("close_time", "23:59")))
    except (ValueError, KeyError):
        return True

    now = datetime.now(tz)
    if now.weekday() not in cfg.get("trading_days", []):
        return False

    current = now.time()
    if open_time <= close_time:
        return open_time <= current <= close_time
    return current >= open_time or current <= close_time


def format_market_schedule(config: dict[str, Any] | None = None) -> str:
    from src.config.settings import load_config

    cfg = get_market_hours_config(config or load_config())
    if not cfg.get("enabled", True):
        return "Piaci nyitvatartás ellenőrzés kikapcsolva."

    days = cfg.get("trading_days") or []
    day_text = ", ".join(WEEKDAY_LABELS[day] for day in days) if days else "nincs nap"
    return (
        f"{cfg.get('timezone')} · {day_text} · "
        f"{cfg.get('open_time')} – {cfg.get('close_time')}"
    )

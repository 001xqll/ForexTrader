from __future__ import annotations


def level_key(level: float, direction: str) -> str:
    return f"{float(level):g}_{direction}"


class LevelHitState:
    """Mely szinteken már nyitottunk (ProTrader levels_hit mintájára)."""

    def __init__(self) -> None:
        self._hit: set[str] = set()

    def is_hit(self, level: float, direction: str) -> bool:
        return level_key(level, direction) in self._hit

    def mark_hit(self, level: float, direction: str) -> None:
        self._hit.add(level_key(level, direction))

    def clear(self) -> None:
        self._hit.clear()

    def keys(self) -> list[str]:
        return sorted(self._hit)

    def has_direction(self, direction: str) -> bool:
        suffix = f"_{direction}"
        return any(key.endswith(suffix) for key in self._hit)

    def clear_direction(self, direction: str) -> None:
        suffix = f"_{direction}"
        self._hit = {key for key in self._hit if not key.endswith(suffix)}

    def restore(self, keys: list[str]) -> None:
        self._hit = set(keys)

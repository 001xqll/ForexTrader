from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_FILE = LOG_DIR / "forextrader.log"
LOGGER_NAME = "forextrader"

_formatter = logging.Formatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")


class GuiLogHandler(logging.Handler):
    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__()
        self._callback = callback
        self.setFormatter(_formatter)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._callback(self.format(record))
        except Exception:
            self.handleError(record)


def setup_logger(gui_callback: Callable[[str], None] | None = None) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(_formatter)
        logger.addHandler(file_handler)

    logger.handlers = [
        handler
        for handler in logger.handlers
        if not isinstance(handler, GuiLogHandler)
    ]

    if gui_callback is not None:
        gui_handler = GuiLogHandler(gui_callback)
        logger.addHandler(gui_handler)

    return logger


def log(message: str, level: int = logging.INFO) -> None:
    logging.getLogger(LOGGER_NAME).log(level, message)


def list_log_files() -> list[Path]:
    if not LOG_DIR.exists():
        return []

    files = [path for path in LOG_DIR.glob("forextrader.log*") if path.is_file()]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return files


def read_log_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def read_recent_log_lines(max_lines: int = 400) -> list[str]:
    if not LOG_FILE.exists():
        return []

    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) <= max_lines:
        return lines
    return lines[-max_lines:]

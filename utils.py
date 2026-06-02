"""
Shared utilities for pi02w Hub.

Logging setup and other cross-cutting helpers.
"""

from __future__ import annotations

import logging

# ---------------------------------------------------------------------------
# Logging – call once at startup from main.py / bot.py
# ---------------------------------------------------------------------------
_LOGGER_INITIALIZED = False


def setup_logging(level: int = logging.INFO) -> None:
    """Configure a simple stdout logger (safe to call multiple times)."""
    global _LOGGER_INITIALIZED
    if _LOGGER_INITIALIZED:
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    _LOGGER_INITIALIZED = True


log = logging.getLogger("aihub")

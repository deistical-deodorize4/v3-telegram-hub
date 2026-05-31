"""
Shared utilities for RaspiPi 02 AI Hub.

Retry logic for Gemini API calls, file-based caching, logging setup,
and other cross-cutting helpers used by multiple modules.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Callable, Optional

from google.genai.errors import ServerError, ClientError

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

# ---------------------------------------------------------------------------
# Gemini API retry helpers
# ---------------------------------------------------------------------------

def extract_retry_delay(error: ClientError) -> int:
    """Pull suggested retry delay (seconds) from a 429 API error."""
    try:
        details = error.response_json.get("error", {}).get("details", [])
        for d in details:
            if "retryDelay" in d:
                return int(d["retryDelay"].replace("s", ""))
    except Exception:
        pass
    return 10


def generate_with_retry(
    call_fn: Callable,
    max_retries: int = 6,
    logger: Optional[logging.Logger] = None,
) -> str:
    """
    Execute a Gemini API call with exponential back-off for 503 / 429 errors.

    Parameters
    ----------
    call_fn : callable returning a response with ``.text``
    max_retries : int
    logger : logger to emit warnings through

    Returns
    -------
    The response object from the successful call.

    Raises
    ------
    RuntimeError after all retries exhausted.
    """
    _log = logger or log
    for attempt in range(1, max_retries + 1):
        try:
            return call_fn()
        except ServerError:
            wait = 3 * attempt
            _log.warning("Server overloaded (503). Retry %d/%d in %ds…", attempt, max_retries, wait)
            time.sleep(wait)
        except ClientError as e:
            if "429" in str(e):
                wait = extract_retry_delay(e)
                _log.warning(
                    "Rate limit hit (429). Waiting %ds before retry %d/%d…",
                    wait,
                    attempt,
                    max_retries,
                )
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"All {max_retries} retries failed after max attempts")

# ---------------------------------------------------------------------------
# Disk-backed cache (MD5-keyed text files)
# ---------------------------------------------------------------------------

def hash_text(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def load_cache(cache_dir: Path, key: str) -> Optional[str]:
    """Return cached content or ``None``."""
    path = cache_dir / f"{key}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def save_cache(cache_dir: Path, key: str, content: str) -> None:
    """Persist *content* to a cache file keyed by *key*."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.txt").write_text(content, encoding="utf-8")

# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------

def validate_float(value: str, min_v: float, max_v: float) -> float:
    """Coerce *value* to float and range-check it."""
    v = float(value)
    if not (min_v <= v <= max_v):
        raise ValueError(f"Value {v} not in [{min_v}, {max_v}]")
    return v


def validate_int(value: str, min_v: int, max_v: int) -> int:
    """Coerce *value* to int and range-check it."""
    v = int(value)
    if not (min_v <= v <= max_v):
        raise ValueError(f"Value {v} not in [{min_v}, {max_v}]")
    return v

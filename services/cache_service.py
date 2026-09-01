"""
services/cache_service.py
===========================
File-based JSON caching for analysis results.
Cache key is SHA-256 of repo identifier. TTL-based expiration.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import time
from typing import Any

import config

logger = logging.getLogger("repolens.cache")


def _ensure_cache_dir():
    os.makedirs(config.CACHE_DIR, exist_ok=True)


def _cache_key(identifier: str) -> str:
    return hashlib.sha256(identifier.encode()).hexdigest()[:16]


def _cache_path(identifier: str, suffix: str = "") -> str:
    _ensure_cache_dir()
    key = _cache_key(identifier)
    name = f"{key}{suffix}.json"
    return os.path.join(config.CACHE_DIR, name)


def get_cached(identifier: str, suffix: str = "") -> Any | None:
    """Retrieve cached data if it exists and hasn't expired."""
    path = _cache_path(identifier, suffix)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        cached_at = data.get("_cached_at", 0)
        if time.time() - cached_at > config.CACHE_TTL_SECONDS:
            logger.debug("Cache expired for %s%s", identifier, suffix)
            os.remove(path)
            return None
        logger.debug("Cache hit for %s%s", identifier, suffix)
        return data.get("payload")
    except Exception as exc:
        logger.warning("Cache read error: %s", exc)
        return None


def set_cached(identifier: str, payload: Any, suffix: str = "") -> None:
    """Store data in cache with timestamp."""
    path = _cache_path(identifier, suffix)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump({"_cached_at": time.time(), "payload": payload}, f, default=str)
        os.replace(tmp_path, path)
        logger.debug("Cached %s%s", identifier, suffix)
    except Exception as exc:
        logger.warning("Cache write error: %s", exc)
        with contextlib.suppress(OSError):
            os.remove(tmp_path)


def invalidate(identifier: str) -> None:
    """Remove all cache entries for an identifier."""
    _ensure_cache_dir()
    key = _cache_key(identifier)
    for filename in os.listdir(config.CACHE_DIR):
        if filename.startswith(key):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(config.CACHE_DIR, filename))
    logger.debug("Cache invalidated for %s", identifier)


def clear_all() -> int:
    """Clear entire cache. Returns number of files removed."""
    _ensure_cache_dir()
    count = 0
    for fname in os.listdir(config.CACHE_DIR):
        if fname.endswith(".json"):
            try:
                os.remove(os.path.join(config.CACHE_DIR, fname))
                count += 1
            except Exception:
                pass
    return count

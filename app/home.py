from __future__ import annotations

import os
import time
from pathlib import Path


def get_home() -> Path:
    override = os.environ.get("RF_HOME")
    path = Path(override) if override else Path.home() / ".remote-folder"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sweep_cache(max_age_seconds: int = 7 * 24 * 3600) -> None:
    cache = get_home() / "cache"
    if not cache.exists():
        return
    cutoff = time.time() - max_age_seconds
    for item in cache.rglob("*"):
        if not item.is_file():
            continue
        try:
            if item.stat().st_mtime < cutoff:
                item.unlink()
        except OSError:
            pass

from __future__ import annotations

import threading
from collections import deque


_lock = threading.Lock()
_items: deque[dict] = deque(maxlen=40)


def add(message: str, level: str = "info") -> None:
    with _lock:
        _items.append({"message": message, "level": level})


def drain() -> list[dict]:
    with _lock:
        items = list(_items)
        _items.clear()
    return items

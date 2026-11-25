"""Read remote CPU and memory from /proc without changing the login identity."""

from __future__ import annotations

import socket
import threading
import time

import paramiko

STATS_COMMAND = "cat /proc/stat /proc/meminfo"
_STALE_SECONDS = 15
_guard = threading.Lock()
_cpu: dict[str, tuple[float, int, int]] = {}


def forget(session_id: str) -> None:
    with _guard:
        _cpu.pop(session_id, None)


def parse_snapshot(text: str) -> tuple[tuple[int, int] | None, dict | None]:
    cpu = None
    fields: dict[str, int] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("cpu "):
            parsed = _cpu_counters(line.split()[1:])
            if parsed is not None:
                cpu = parsed
            continue
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        parts = rest.split()
        if parts and parts[0].isdigit():
            fields[key] = int(parts[0])
    total = fields.get("MemTotal")
    if not total:
        return cpu, None
    if "MemAvailable" in fields:
        available = fields["MemAvailable"]
    else:
        available = fields.get("MemFree", 0) + fields.get("Buffers", 0) + fields.get("Cached", 0)
    available = min(total, max(0, available))
    used = total - available
    return cpu, {
        "used": used * 1024,
        "total": total * 1024,
        "percent": round(used / total * 100, 1),
    }


def consume_cpu(session_id: str, total: int, idle: int, now: float | None = None) -> float | None:
    moment = time.monotonic() if now is None else now
    with _guard:
        previous = _cpu.get(session_id)
        _cpu[session_id] = (moment, total, idle)
        if previous is None:
            return None
        then, prev_total, prev_idle = previous
        if moment < then or moment - then > _STALE_SECONDS:
            return None
        delta_total = total - prev_total
        delta_idle = idle - prev_idle
        if delta_total <= 0 or delta_idle < 0 or delta_idle > delta_total:
            return None
        return round((1 - delta_idle / delta_total) * 100, 1)


def interpret(session_id: str, text: str | None) -> dict:
    if not text:
        return {"supported": False}
    sample, memory = parse_snapshot(text)
    if memory is None:
        return {"supported": False}
    cpu = consume_cpu(session_id, *sample) if sample is not None else None
    return {"supported": True, "cpu": cpu, "memory": memory}


class StatsRetry(Exception):
    """The SSH session is still up, but this sample did not arrive."""


def read(session_id: str) -> dict:
    from app.pool import pool

    try:
        text = pool.call(session_id, lambda live: capture(live.client))
    except StatsRetry:
        return {"supported": True, "pending": True}
    return interpret(session_id, text)


def capture(client) -> str | None:
    stdout = None
    try:
        stdin, stdout, stderr = client.exec_command(STATS_COMMAND, timeout=5)
        try:
            stdin.close()
        except Exception:
            pass
        data = stdout.read(65536)
        try:
            stderr.read(4096)
        except Exception:
            pass
        code = stdout.channel.recv_exit_status()
    except (socket.timeout, TimeoutError):
        return None
    except (EOFError, OSError, paramiko.SSHException):
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            raise
        raise StatsRetry() from None
    finally:
        if stdout is not None:
            try:
                stdout.channel.close()
            except Exception:
                pass
    if code not in (0, None):
        return None
    return data.decode("utf-8", "replace")


def _cpu_counters(parts: list[str]) -> tuple[int, int] | None:
    nums: list[int] = []
    for item in parts:
        if not item.isdigit():
            return None
        nums.append(int(item))
    if len(nums) < 4:
        return None
    # user, nice, system, idle, iowait, irq, softirq, steal. guest is already inside user.
    head = nums[:8]
    idle = head[3] + (head[4] if len(head) > 4 else 0)
    return sum(head), idle

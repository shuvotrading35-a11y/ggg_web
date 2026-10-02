"""
utils/formatters.py — Message formatting helpers.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone


def fmt_uptime(started_at: datetime | None) -> str:
    if not started_at:
        return "—"
    delta = datetime.now(timezone.utc) - started_at.replace(tzinfo=timezone.utc)
    total = int(delta.total_seconds())
    h, rem = divmod(total, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s"


def fmt_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def fmt_ram(mb: float) -> str:
    if mb >= 1024:
        return f"{mb/1024:.1f} GB"
    return f"{mb:.0f} MB"


def fmt_disk(used: int, total: int) -> str:
    used_g  = used / (1024 ** 3)
    total_g = total / (1024 ** 3)
    pct     = (used / total * 100) if total else 0
    bar     = _bar(pct)
    return f"{used_g:.1f} GB / {total_g:.1f} GB  {bar}"


def _bar(pct: float, width: int = 10) -> str:
    filled = math.floor(pct / 100 * width)
    return "█" * filled + "░" * (width - filled) + f"  {pct:.0f}%"


def ts(dt: datetime | None) -> str:
    if not dt:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M")

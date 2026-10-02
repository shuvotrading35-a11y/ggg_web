"""
services/resource_monitor.py — VPS-level and per-bot resource monitoring.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil

from config import PERF_HISTORY_DAYS, PERF_SAMPLE_INTERVAL_SECONDS
from database import AsyncSessionLocal, Bot, PerfSample
from sqlalchemy import delete, select


# ── VPS stats ─────────────────────────────────────────────────────────────────

def vps_stats() -> dict:
    cpu    = psutil.cpu_percent(interval=0.5)
    mem    = psutil.virtual_memory()
    disk   = shutil.disk_usage("/")
    load   = psutil.getloadavg()[0]
    uptime = _vps_uptime()
    return {
        "cpu":       cpu,
        "ram_used":  mem.used  / (1024**2),    # MB
        "ram_total": mem.total / (1024**2),
        "disk_used": disk.used,
        "disk_total":disk.total,
        "load":      load,
        "uptime":    uptime,
    }


def _vps_uptime() -> str:
    boot  = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc)
    delta = datetime.now(timezone.utc) - boot
    d, rem = divmod(int(delta.total_seconds()), 86400)
    h, rem = divmod(rem, 3600)
    m      = rem // 60
    if d:
        return f"{d}d {h}h {m}m"
    return f"{h}h {m}m"


# ── Per-bot stats ─────────────────────────────────────────────────────────────

def bot_resource_info(pid: int | None) -> dict:
    info = {"cpu": 0.0, "ram_mb": 0.0, "alive": False}
    if not pid:
        return info
    try:
        p = psutil.Process(pid)
        info["cpu"]    = p.cpu_percent(interval=0.2)
        info["ram_mb"] = p.memory_info().rss / (1024**2)
        info["alive"]  = p.is_running()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return info


def bot_disk_usage(directory: str) -> int:
    """Return total bytes used by a bot directory."""
    total = 0
    for f in Path(directory).rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


# ── Background sampler ────────────────────────────────────────────────────────

async def perf_sampler_loop() -> None:
    """Collect CPU/RAM samples for all running bots every N seconds."""
    while True:
        await asyncio.sleep(PERF_SAMPLE_INTERVAL_SECONDS)
        try:
            async with AsyncSessionLocal() as s:
                result = await s.execute(select(Bot).where(Bot.pid != None))
                bots   = result.scalars().all()

                for bot in bots:
                    info = bot_resource_info(bot.pid)
                    s.add(PerfSample(
                        bot_id=bot.id,
                        cpu_pct=info["cpu"],
                        ram_mb=info["ram_mb"],
                    ))

                # Purge old samples
                cutoff = datetime.now(timezone.utc) - timedelta(days=PERF_HISTORY_DAYS)
                await s.execute(delete(PerfSample).where(PerfSample.sampled_at < cutoff))
                await s.commit()
        except Exception:
            pass


async def get_perf_history(bot_id: int, hours: int = 24) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(PerfSample)
            .where(PerfSample.bot_id == bot_id, PerfSample.sampled_at >= cutoff)
            .order_by(PerfSample.sampled_at)
        )
        samples = result.scalars().all()
    return [{"time": p.sampled_at, "cpu": p.cpu_pct, "ram": p.ram_mb} for p in samples]

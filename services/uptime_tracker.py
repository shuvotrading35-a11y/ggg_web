"""
services/uptime_tracker.py — Track per-bot uptime percentage and daily stats.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from config import BotState
from database import AsyncSessionLocal, Bot, UptimeRecord
from sqlalchemy import select


async def record_uptime_check() -> None:
    """Called every minute — records whether each bot is up or down."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async with AsyncSessionLocal() as s:
        result = await s.execute(select(Bot))
        bots   = result.scalars().all()

        for bot in bots:
            is_up = bot.state == BotState.RUNNING

            # Get or create today's record
            rec_result = await s.execute(
                select(UptimeRecord).where(
                    UptimeRecord.bot_id == bot.id,
                    UptimeRecord.date   == today,
                )
            )
            rec = rec_result.scalars().first()

            if not rec:
                rec = UptimeRecord(bot_id=bot.id, date=today)
                s.add(rec)

            rec.total_checks += 1
            if is_up:
                rec.up_checks += 1
            rec.uptime_pct = (rec.up_checks / rec.total_checks) * 100

        await s.commit()


async def get_uptime_summary(bot_id: int, days: int = 30) -> list[dict]:
    """Return uptime records for the last N days."""
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(UptimeRecord)
            .where(UptimeRecord.bot_id == bot_id)
            .order_by(UptimeRecord.date.desc())
            .limit(days)
        )
        records = result.scalars().all()

    return [
        {
            "date":        r.date,
            "uptime_pct":  r.uptime_pct,
            "crash_count": r.crash_count,
        }
        for r in records
    ]


async def get_monthly_uptime(bot_id: int) -> float:
    """Return average uptime % for current month."""
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(UptimeRecord).where(
                UptimeRecord.bot_id == bot_id,
                UptimeRecord.date.like(f"{month}%"),
            )
        )
        records = result.scalars().all()

    if not records:
        return 0.0
    return sum(r.uptime_pct for r in records) / len(records)


async def uptime_loop() -> None:
    """Background loop — samples every 60 seconds."""
    while True:
        await asyncio.sleep(60)
        try:
            await record_uptime_check()
        except Exception:
            pass

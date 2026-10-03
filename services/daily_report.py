"""
services/daily_report.py — Generate and send daily summary report to Admin.
"""

from __future__ import annotations

from datetime import datetime, timezone

from config import BotState, DAILY_REPORT_TIME
from database import AsyncSessionLocal, Bot, UptimeRecord
from services.resource_monitor import vps_stats
from services.uptime_tracker import get_monthly_uptime
from sqlalchemy import select, func


async def generate_daily_report() -> str:
    """Build the daily report text."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now   = datetime.now(timezone.utc).strftime("%H:%M UTC")

    async with AsyncSessionLocal() as s:
        total   = (await s.execute(select(func.count()).select_from(Bot))).scalar()
        running = (await s.execute(select(func.count()).select_from(Bot).where(Bot.state == BotState.RUNNING))).scalar()
        crashed = (await s.execute(select(func.count()).select_from(Bot).where(Bot.state == BotState.CRASHED))).scalar()
        stopped = (await s.execute(select(func.count()).select_from(Bot).where(Bot.state == BotState.STOPPED))).scalar()

        # Today's crash count across all bots
        result = await s.execute(
            select(func.sum(UptimeRecord.crash_count)).where(UptimeRecord.date == today)
        )
        total_crashes = result.scalar() or 0

        # Bot-wise uptime
        result2 = await s.execute(select(Bot).order_by(Bot.name))
        bots    = result2.scalars().all()

    stats = vps_stats()

    lines = [
        f"📊 <b>Daily Report — {today}</b>",
        f"🕐 Generated: {now}\n",
        f"🖥 <b>VPS Status</b>",
        f"CPU:    {stats['cpu']:.1f}%",
        f"RAM:    {stats['ram_used']:.0f} MB / {stats['ram_total']:.0f} MB",
        f"Uptime: {stats['uptime']}\n",
        f"🤖 <b>Bots Overview</b>",
        f"Total:   {total}",
        f"🟢 Running: {running}",
        f"🔴 Stopped: {stopped}",
        f"⚠️ Crashed: {crashed}",
        f"💥 Crashes today: {total_crashes}\n",
        f"📈 <b>Per-Bot Uptime (this month)</b>",
    ]

    for bot in bots:
        uptime_pct = await get_monthly_uptime(bot.id)
        icon = "✅" if uptime_pct >= 95 else ("⚠️" if uptime_pct >= 80 else "❌")
        lines.append(f"{icon} {bot.name}: {uptime_pct:.1f}%")

    lines.append("\n<i>Shuvo Hosting Manager</i>")
    return "\n".join(lines)


async def send_daily_report() -> None:
    """Send the daily report to all admins."""
    from services.notifier import notify
    text = await generate_daily_report()
    await notify(text, critical=False, event_type="on_reboot_restore")


def schedule_daily_report(scheduler) -> None:
    """Add daily report job to APScheduler."""
    try:
        hour, minute = DAILY_REPORT_TIME.split(":")
        scheduler.add_job(
            send_daily_report,
            trigger="cron",
            hour=int(hour),
            minute=int(minute),
            id="daily_report",
            replace_existing=True,
        )
    except Exception:
        pass

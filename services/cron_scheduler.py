"""
services/cron_scheduler.py — APScheduler-based task runner for bot scheduled actions.
"""

from __future__ import annotations

from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from database import AsyncSessionLocal, ScheduledTask
from sqlalchemy import select

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


async def dispatch_action(task_id: int, action: str, bot_id: int | None) -> None:
    from services.process_manager import start_bot, stop_bot, restart_bot
    from services.logger import clear_bot_logs

    action_map = {
        "start":      lambda: start_bot(bot_id) if bot_id else None,
        "stop":       lambda: stop_bot(bot_id) if bot_id else None,
        "restart":    lambda: restart_bot(bot_id) if bot_id else None,
        "clear_logs": lambda: clear_bot_logs(bot_id) if bot_id else None,
    }

    fn = action_map.get(action)
    if fn:
        try:
            result = fn()
            if result:
                await result
        except Exception:
            pass

    async with AsyncSessionLocal() as s:
        task = await s.get(ScheduledTask, task_id)
        if task:
            task.last_run = datetime.now(timezone.utc)
            await s.commit()


async def load_schedules() -> None:
    """Load all enabled scheduled tasks from DB into APScheduler."""
    sched = get_scheduler()

    # Clear existing jobs
    sched.remove_all_jobs()

    async with AsyncSessionLocal() as s:
        result = await s.execute(select(ScheduledTask).where(ScheduledTask.enabled == True))
        tasks  = result.scalars().all()

    for task in tasks:
        try:
            sched.add_job(
                dispatch_action,
                trigger=CronTrigger.from_crontab(task.cron_expr),
                args=[task.id, task.action, task.bot_id],
                id=f"task_{task.id}",
                replace_existing=True,
            )
        except Exception:
            pass


def start_scheduler() -> None:
    sched = get_scheduler()
    if not sched.running:
        sched.start()


def stop_scheduler() -> None:
    sched = get_scheduler()
    if sched.running:
        sched.shutdown(wait=False)

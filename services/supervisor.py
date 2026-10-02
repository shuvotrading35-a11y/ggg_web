"""
services/supervisor.py — Watches running bots and auto-restarts crashed ones.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import psutil

from config import BotState, MAX_RESTART_ATTEMPTS, RESTART_WINDOW_MINUTES
from database import AsyncSessionLocal, Bot
from sqlalchemy import select

# bot_id → list of crash timestamps
_crash_times: dict[int, list[datetime]] = defaultdict(list)

# Callback set from outside (notifier)
_notify_callback = None


def set_notify_callback(cb) -> None:
    global _notify_callback
    _notify_callback = cb


async def _notify(msg: str, bot_name: str, bot_id: int) -> None:
    if _notify_callback:
        await _notify_callback(msg, bot_name, bot_id)


async def _check_bot(bot: Bot) -> None:
    from services.process_manager import start_bot, _pid_alive

    if bot.state != BotState.RUNNING or not bot.pid:
        return

    alive = _pid_alive(bot.pid)
    if alive:
        return

    # Bot crashed
    now = datetime.now(timezone.utc)
    _crash_times[bot.id].append(now)

    # Prune old timestamps outside the window
    window_start = now - timedelta(minutes=RESTART_WINDOW_MINUTES)
    _crash_times[bot.id] = [t for t in _crash_times[bot.id] if t >= window_start]

    async with AsyncSessionLocal() as s:
        bot_obj = await s.get(Bot, bot.id)
        if not bot_obj:
            return

        if bot_obj.crash_loop:
            return  # already flagged, do nothing

        crash_count = len(_crash_times[bot.id])

        if crash_count >= MAX_RESTART_ATTEMPTS:
            bot_obj.crash_loop = True
            bot_obj.state      = BotState.CRASHED
            bot_obj.pid        = None
            await s.commit()
            await _notify(
                f"⚠️ <b>Crash Loop Detected</b>\n\nBot: <b>{bot_obj.name}</b>\n"
                f"Crashed {crash_count}x in {RESTART_WINDOW_MINUTES} minutes.\n"
                f"Auto-restart has been <b>disabled</b>.\n\n"
                f"Fix the bot and restart manually.",
                bot_obj.name, bot_obj.id,
            )
            return

        # read exit code from last stdout log if possible
        bot_obj.state         = BotState.CRASHED
        bot_obj.restart_count = (bot_obj.restart_count or 0) + 1
        bot_obj.pid           = None
        await s.commit()

    await _notify(
        f"🚨 <b>Bot Crashed</b>\n\nBot: <b>{bot.name}</b>\n"
        f"Auto-restarting... (attempt {crash_count}/{MAX_RESTART_ATTEMPTS})",
        bot.name, bot.id,
    )

    await asyncio.sleep(2)
    await start_bot(bot.id)


async def supervisor_loop() -> None:
    """Main loop — runs forever, checks every 15 seconds."""
    while True:
        try:
            async with AsyncSessionLocal() as s:
                result = await s.execute(
                    select(Bot).where(Bot.state == BotState.RUNNING)
                )
                bots = result.scalars().all()

            for bot in bots:
                await _check_bot(bot)
        except Exception as e:
            pass  # don't crash the supervisor

        await asyncio.sleep(15)

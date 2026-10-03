"""
services/restart_rules.py — Conditional auto-restart rules per bot.
Conditions: cpu_gt, ram_gt, uptime_gt (hours), error_count_gt.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from config import BotState
from database import AsyncSessionLocal, Bot, RestartRule
from services.process_manager import restart_bot, get_process_info
from sqlalchemy import select


async def evaluate_rules() -> None:
    """Check all enabled rules and restart bots that violate them."""
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Bot).where(Bot.state == BotState.RUNNING, Bot.pid != None)
        )
        bots = result.scalars().all()

    for bot in bots:
        await _check_bot_rules(bot)


async def _check_bot_rules(bot: Bot) -> None:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(RestartRule).where(RestartRule.bot_id == bot.id, RestartRule.enabled == True)
        )
        rules = result.scalars().all()

    if not rules:
        return

    info = await get_process_info(bot)
    uptime_hours = 0.0
    if bot.started_at:
        delta = datetime.now(timezone.utc) - bot.started_at.replace(tzinfo=timezone.utc)
        uptime_hours = delta.total_seconds() / 3600

    for rule in rules:
        triggered = False

        if rule.condition == "cpu_gt" and info["cpu"] > rule.threshold:
            triggered = True
            reason = f"CPU {info['cpu']:.1f}% > {rule.threshold}%"

        elif rule.condition == "ram_gt" and info["ram_mb"] > rule.threshold:
            triggered = True
            reason = f"RAM {info['ram_mb']:.0f} MB > {rule.threshold} MB"

        elif rule.condition == "uptime_gt" and uptime_hours > rule.threshold:
            triggered = True
            reason = f"Uptime {uptime_hours:.1f}h > {rule.threshold}h"

        elif rule.condition == "error_count_gt" and (bot.restart_count or 0) > rule.threshold:
            triggered = True
            reason = f"Restarts {bot.restart_count} > {rule.threshold}"

        if triggered:
            from services.notifier import notify
            await notify(
                f"🔄 <b>Auto-Restart Rule Triggered</b>\n\n"
                f"Bot: <b>{bot.name}</b>\n"
                f"Reason: {reason}",
                critical=False,
            )
            await restart_bot(bot.id)
            break  # Only one restart per cycle


async def rules_loop() -> None:
    """Check rules every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        try:
            await evaluate_rules()
        except Exception:
            pass

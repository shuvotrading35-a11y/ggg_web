"""
services/notifier.py — Send admin notifications with quiet-hours filtering.
"""

from __future__ import annotations

from datetime import datetime, timezone

from telegram import Bot as TGBot

from config import ADMIN_IDS, QUIET_HOURS_START, QUIET_HOURS_END
from database import AsyncSessionLocal, NotificationConfig
from sqlalchemy import select

_bot_instance: TGBot | None = None


def set_bot(bot: TGBot) -> None:
    global _bot_instance
    _bot_instance = bot


def _is_quiet_hour() -> bool:
    hour = datetime.now(timezone.utc).hour
    if QUIET_HOURS_START <= QUIET_HOURS_END:
        return QUIET_HOURS_START <= hour < QUIET_HOURS_END
    return hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END


async def _get_config() -> NotificationConfig | None:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(NotificationConfig))
        return result.scalars().first()


async def notify(
    message: str,
    *,
    critical: bool = False,
    event_type: str | None = None,
) -> None:
    """
    Send message to all admins.
    Non-critical messages are suppressed during quiet hours.
    event_type maps to NotificationConfig columns.
    """
    if not _bot_instance:
        return

    cfg = await _get_config()

    # Check if this event type is enabled
    if cfg and event_type:
        enabled = getattr(cfg, event_type, True)
        if not enabled:
            return

    # Quiet hours check (skip for critical)
    if not critical and _is_quiet_hour():
        return

    for admin_id in ADMIN_IDS:
        try:
            await _bot_instance.send_message(
                chat_id=admin_id,
                text=message,
                parse_mode="HTML",
            )
        except Exception:
            pass


# ── Typed convenience wrappers ────────────────────────────────────────────────

async def notify_crash(bot_name: str, bot_id: int) -> None:
    await notify(
        f"🚨 <b>Bot Crashed</b>\n\nBot: <b>{bot_name}</b>\n"
        f"The system is attempting auto-restart.",
        critical=True, event_type="on_crash",
    )


async def notify_crash_loop(bot_name: str) -> None:
    await notify(
        f"⚠️ <b>Crash Loop Detected</b>\n\nBot: <b>{bot_name}</b>\n"
        f"Auto-restart disabled. Please fix and restart manually.",
        critical=True, event_type="on_crash_loop",
    )


async def notify_started(bot_name: str) -> None:
    await notify(f"✅ <b>Bot Started</b>: {bot_name}", event_type="on_start")


async def notify_stopped(bot_name: str) -> None:
    await notify(f"⏹ <b>Bot Stopped</b>: {bot_name}", event_type="on_stop")


async def notify_reboot_restore(names: list[str]) -> None:
    joined = "\n".join(f"• {n}" for n in names)
    await notify(
        f"🔄 <b>VPS Reboot — Bots Restored</b>\n\n{joined}",
        critical=False, event_type="on_reboot_restore",
    )


async def notify_high_cpu(bot_name: str, pct: float) -> None:
    await notify(
        f"🔥 <b>High CPU</b>: {bot_name} — {pct:.1f}%",
        critical=False, event_type="on_high_cpu",
    )


async def notify_low_disk(free_gb: float) -> None:
    await notify(
        f"💾 <b>Low Disk Space</b>: only {free_gb:.1f} GB free",
        critical=True, event_type="on_low_disk",
    )

"""
handlers/settings.py — Bot settings, notification config, quick slash commands.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from database import AsyncSessionLocal, Bot, NotificationConfig
from keyboards.main import main_menu
from services.audit import log_action
from services.process_manager import restart_bot, start_bot, stop_bot
from services.logger import combined_tail
from utils.security import is_admin
from sqlalchemy import select


async def show_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return

    async with AsyncSessionLocal() as s:
        result = await s.execute(select(NotificationConfig))
        cfg    = result.scalars().first()

    def _icon(v: bool) -> str:
        return "✅" if v else "☐"

    text = (
        "⚙️ <b>Notification Settings</b>\n\n"
        f"{_icon(cfg.on_crash)}      Bot Crashed\n"
        f"{_icon(cfg.on_crash_loop)} Crash Loop\n"
        f"{_icon(cfg.on_start)}      Bot Started\n"
        f"{_icon(cfg.on_stop)}       Bot Stopped\n"
        f"{_icon(cfg.on_high_cpu)}   High CPU (&gt;80%)\n"
        f"{_icon(cfg.on_high_ram)}   High RAM (&gt;90%)\n"
        f"{_icon(cfg.on_low_disk)}   Low Disk (&lt;1 GB)\n"
        f"{_icon(cfg.on_reboot_restore)} VPS Reboot Restore\n\n"
        f"Quiet Hours: {cfg.quiet_hours_start:02d}:00–{cfg.quiet_hours_end:02d}:00 UTC"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔔 Toggle Crash",     callback_data="notif_toggle:on_crash"),
            InlineKeyboardButton("🔔 Toggle Start",     callback_data="notif_toggle:on_start"),
        ],
        [
            InlineKeyboardButton("🔔 Toggle Stop",      callback_data="notif_toggle:on_stop"),
            InlineKeyboardButton("🔔 Toggle CPU",       callback_data="notif_toggle:on_high_cpu"),
        ],
        [
            InlineKeyboardButton("🔔 Toggle RAM",       callback_data="notif_toggle:on_high_ram"),
            InlineKeyboardButton("🔔 Toggle Disk",      callback_data="notif_toggle:on_low_disk"),
        ],
    ])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def cb_notif_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    field  = query.data.split(":")[1]

    async with AsyncSessionLocal() as s:
        result = await s.execute(select(NotificationConfig))
        cfg    = result.scalars().first()
        current = getattr(cfg, field, False)
        setattr(cfg, field, not current)
        await s.commit()

    await query.answer(f"{'Enabled' if not current else 'Disabled'}: {field}")


# ── Quick slash commands ──────────────────────────────────────────────────────

async def _find_bot_by_name(name: str) -> Bot | None:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(Bot).where(Bot.name.ilike(name)))
        return result.scalars().first()


async def cmd_restart_bot(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /restart <bot_name>")
        return
    name = " ".join(args)
    bot  = await _find_bot_by_name(name)
    if not bot:
        await update.message.reply_text(f"❌ Bot '{name}' not found.")
        return
    ok, msg = await restart_bot(bot.id)
    await update.message.reply_text(msg)
    await log_action(f"Quick restart: {bot.name}", bot_name=bot.name)


async def cmd_stop_bot(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /stop <bot_name>")
        return
    name = " ".join(args)
    bot  = await _find_bot_by_name(name)
    if not bot:
        await update.message.reply_text(f"❌ Bot '{name}' not found.")
        return
    ok, msg = await stop_bot(bot.id)
    await update.message.reply_text(msg)
    await log_action(f"Quick stop: {bot.name}", bot_name=bot.name)


async def cmd_start_bot(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /start_bot <bot_name>")
        return
    name = " ".join(args)
    bot  = await _find_bot_by_name(name)
    if not bot:
        await update.message.reply_text(f"❌ Bot '{name}' not found.")
        return
    ok, msg = await start_bot(bot.id)
    await update.message.reply_text(msg)
    await log_action(f"Quick start: {bot.name}", bot_name=bot.name)


async def cmd_logs_quick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /logs <bot_name>")
        return
    name = " ".join(args)
    bot  = await _find_bot_by_name(name)
    if not bot:
        await update.message.reply_text(f"❌ Bot '{name}' not found.")
        return
    logs = combined_tail(bot.directory)
    await update.message.reply_text(
        f"📜 <b>Logs — {bot.name}</b>\n\n<pre>{logs[:3500]}</pre>",
        parse_mode="HTML",
    )


async def cmd_backup_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    from services.file_manager import backup_bot
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(Bot))
        bots   = result.scalars().all()
    results = []
    for bot in bots:
        path = await backup_bot(bot.id)
        results.append(f"{'✅' if path else '❌'} {bot.name}")
    await update.message.reply_text(
        "📦 <b>Backup All — Done</b>\n\n" + "\n".join(results),
        parse_mode="HTML",
    )
    await log_action("Manual backup all bots")

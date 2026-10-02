"""
handlers/stats.py — Server stats, bulk actions, maintenance mode.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import BotState, STATE_EMOJI
from database import AsyncSessionLocal, Bot
from keyboards.bots import bulk_kb
from keyboards.main import main_menu
from services.audit import log_action
from services.process_manager import restart_bot, start_bot, stop_bot
from services.resource_monitor import vps_stats
from utils.formatters import fmt_ram, fmt_disk, fmt_bytes
from utils.security import is_admin
from sqlalchemy import select, func


async def show_server_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return

    stats = vps_stats()
    async with AsyncSessionLocal() as s:
        total   = (await s.execute(select(func.count()).select_from(Bot))).scalar()
        running = (await s.execute(select(func.count()).select_from(Bot).where(Bot.state == BotState.RUNNING))).scalar()
        stopped = (await s.execute(select(func.count()).select_from(Bot).where(Bot.state == BotState.STOPPED))).scalar()
        crashed = (await s.execute(select(func.count()).select_from(Bot).where(Bot.state == BotState.CRASHED))).scalar()

    text = (
        "🖥 <b>VPS Statistics</b>\n\n"
        f"CPU:    {stats['cpu']:.1f}%\n"
        f"RAM:    {fmt_ram(stats['ram_used'])} / {fmt_ram(stats['ram_total'])}\n"
        f"Disk:   {fmt_disk(stats['disk_used'], stats['disk_total'])}\n"
        f"Load:   {stats['load']:.2f}\n"
        f"Uptime: {stats['uptime']}\n\n"
        f"🤖 Hosted Bots: <b>{total}</b>\n"
        f"🟢 Running:     <b>{running}</b>\n"
        f"🔴 Stopped:     <b>{stopped}</b>\n"
        f"⚠️ Crashed:    <b>{crashed}</b>\n"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu())


async def show_bulk_actions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "⚡ <b>Bulk Actions</b>\n\nApply an action to all hosted bots:",
        parse_mode="HTML",
        reply_markup=bulk_kb(),
    )


async def cb_bulk_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Starting all bots…")
    await _bulk_action("start", query)


async def cb_bulk_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Stopping all bots…")
    await _bulk_action("stop", query)


async def cb_bulk_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Restarting all bots…")
    await _bulk_action("restart", query)


async def cb_bulk_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Backing up all bots…")
    from services.file_manager import backup_bot
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(Bot))
        bots   = result.scalars().all()
    results = []
    for bot in bots:
        path = await backup_bot(bot.id)
        results.append(f"{'✅' if path else '❌'} {bot.name}")
    await query.edit_message_text("📦 <b>Backup Results</b>\n\n" + "\n".join(results), parse_mode="HTML")
    await log_action("Bulk backup all bots")


async def cb_bulk_clear_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Clearing logs…")
    from services.logger import clear_bot_logs
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(Bot))
        bots   = result.scalars().all()
    for bot in bots:
        await clear_bot_logs(bot_dir=bot.directory)
    await query.edit_message_text("🧹 All logs cleared.")
    await log_action("Bulk clear all logs")


async def _bulk_action(action: str, query) -> None:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(Bot))
        bots   = result.scalars().all()

    fn_map = {"start": start_bot, "stop": stop_bot, "restart": restart_bot}
    fn = fn_map[action]

    results = []
    for bot in bots:
        ok, _ = await fn(bot.id)
        emoji  = "✅" if ok else "❌"
        results.append(f"{emoji} {bot.name}")

    await query.edit_message_text(
        f"⚡ <b>Bulk {action.capitalize()} — Done</b>\n\n" + "\n".join(results),
        parse_mode="HTML",
    )
    await log_action(f"Bulk {action} all bots")


# ── Maintenance Mode ───────────────────────────────────────────────────────────

_maintenance_active = False
_maintenance_stopped: list[int] = []


async def toggle_maintenance(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    global _maintenance_active, _maintenance_stopped
    if not is_admin(update.effective_user.id):
        return

    if _maintenance_active:
        # End maintenance — restore previously running bots
        count = 0
        for bot_id in _maintenance_stopped:
            ok, _ = await start_bot(bot_id)
            if ok:
                count += 1
        _maintenance_stopped.clear()
        _maintenance_active = False
        await update.message.reply_text(
            f"✅ <b>Maintenance Mode Ended</b>\n\nRestored {count} bot(s).",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
    else:
        # Start maintenance — stop all running bots
        async with AsyncSessionLocal() as s:
            result = await s.execute(select(Bot).where(Bot.state == BotState.RUNNING))
            bots   = result.scalars().all()

        _maintenance_stopped = [b.id for b in bots]
        for bot_id in _maintenance_stopped:
            await stop_bot(bot_id)

        _maintenance_active = True
        await update.message.reply_text(
            f"🔧 <b>Maintenance Mode Active</b>\n\n"
            f"Stopped {len(_maintenance_stopped)} bot(s).\n\n"
            f"Run /maintenance again to end and restore all bots.",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
    await log_action(f"Maintenance mode {'activated' if _maintenance_active else 'deactivated'}")

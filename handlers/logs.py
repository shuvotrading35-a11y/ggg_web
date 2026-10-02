"""
handlers/logs.py — View, download, clear bot logs.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from database import AsyncSessionLocal, Bot
from keyboards.bots import logs_kb
from services.logger import clear_bot_logs, combined_tail, log_file_path
from utils.security import is_admin


async def cb_bot_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        await query.edit_message_text("❌ Bot not found.")
        return

    logs = combined_tail(bot.directory)
    text = f"📜 <b>Logs — {bot.name}</b>\n\n<pre>{logs[:3500]}</pre>"

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=logs_kb(bot_id))


async def cb_bot_logs_download(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Sending log files…")
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        return

    for fname in ("stdout.log", "stderr.log"):
        path = log_file_path(bot.directory, fname)
        if path.exists() and path.stat().st_size > 0:
            await query.message.reply_document(
                document=open(path, "rb"),
                filename=f"{bot.slug}_{fname}",
            )


async def cb_bot_logs_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Clearing logs…")
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if bot:
        await clear_bot_logs(bot_dir=bot.directory)
        await query.edit_message_text("🧹 Logs cleared.", reply_markup=logs_kb(bot_id))


async def show_system_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return

    from services.audit import get_recent_logs
    from utils.formatters import ts

    logs = await get_recent_logs(30)
    if not logs:
        await update.message.reply_text("📜 No activity recorded yet.")
        return

    lines = ["📋 <b>Admin Activity Log</b>\n"]
    for log in logs:
        lines.append(f"<code>{ts(log.created_at)}</code> — {log.action}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

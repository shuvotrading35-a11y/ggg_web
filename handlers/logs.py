"""
handlers/logs.py — View, download, clear bot logs.
"""

from __future__ import annotations

import html

from telegram import Update
from telegram.ext import ContextTypes

from database import AsyncSessionLocal, Bot
from keyboards.bots import logs_kb
from services.logger import clear_bot_logs, combined_tail, log_file_path
from utils.security import is_admin


# Telegram message body limit is 4096 chars.
# Reserve some room for the header + wrapper tags.
_MAX_PRE = 3500


def _safe_log_html(logs: str, max_len: int = _MAX_PRE) -> str:
    """
    Escape log text so it can be safely embedded in <pre>…</pre>
    for Telegram's HTML parser.

    - Escapes & < > (quote=False keeps quotes readable)
    - Trims from the END (keeps most recent logs) if too long
    - Never leaves a partial HTML entity like "&lt" without ";" at the start
    """
    if not logs:
        return "(no logs yet)"

    # Trim raw text first (keep tail = most recent)
    if len(logs) > max_len:
        logs = "…(truncated)…\n" + logs[-max_len:]

    # Escape — safest for Telegram HTML mode
    safe = html.escape(logs, quote=False)

    # If trimming raw left a dangling entity at the start,
    # drop up to the first semicolon (entities are short).
    if safe.startswith("&") and ";" not in safe[:12]:
        # find first ';' if any
        idx = safe.find(";")
        if 0 < idx < 12:
            safe = safe[idx + 1:]

    return safe


async def cb_bot_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        await query.edit_message_text("❌ Bot not found.")
        return

    try:
        logs = combined_tail(bot.directory) or "(empty log)"
    except Exception as e:
        logs = f"(failed to read logs: {e})"

    safe_logs = _safe_log_html(logs)
    text = f"📜 <b>Logs — {html.escape(bot.name)}</b>\n\n<pre>{safe_logs}</pre>"

    try:
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=logs_kb(bot_id)
        )
    except Exception as e:
        # Fallback: send fresh message without buttons if edit fails
        try:
            await query.message.reply_text(
                f"📜 <b>Logs — {html.escape(bot.name)}</b>\n\n<pre>{safe_logs}</pre>",
                parse_mode="HTML",
            )
        except Exception:
            await query.message.reply_text(
                f"❌ Could not display logs: {e}"
            )


async def cb_bot_logs_download(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Sending log files…")
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        return

    sent = False
    for fname in ("stdout.log", "stderr.log"):
        try:
            path = log_file_path(bot.directory, fname)
            if path.exists() and path.stat().st_size > 0:
                await query.message.reply_document(
                    document=open(path, "rb"),
                    filename=f"{bot.slug}_{fname}",
                )
                sent = True
        except Exception:
            continue

    if not sent:
        try:
            await query.message.reply_text("📭 No log files available.")
        except Exception:
            pass


async def cb_bot_logs_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Clearing logs…")
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if bot:
        try:
            await clear_bot_logs(bot_dir=bot.directory)
        except Exception as e:
            await query.edit_message_text(f"❌ Clear failed: {e}")
            return
        await query.edit_message_text(
            "🧹 Logs cleared.", reply_markup=logs_kb(bot_id)
        )


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
        # Escape action text in case it contains <, >, &
        action = html.escape(str(log.action), quote=False)
        lines.append(f"<code>{ts(log.created_at)}</code> — {action}")

    text = "\n".join(lines)
    # Safety trim
    if len(text) > 4000:
        text = text[:4000] + "\n…(truncated)"

    try:
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception:
        # Last-resort: send as plain text
        await update.message.reply_text(text)
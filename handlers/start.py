"""
handlers/start.py — /start command and main menu dispatcher.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import BotState, STATE_EMOJI
from database import AsyncSessionLocal, Bot
from keyboards.main import main_menu
from utils.security import is_admin
from sqlalchemy import select, func


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text(
            "⛔ <b>Access Denied</b>\n\nThis is a private admin-only system.",
            parse_mode="HTML",
        )
        return

    async with AsyncSessionLocal() as s:
        total   = (await s.execute(select(func.count()).select_from(Bot))).scalar()
        running = (await s.execute(
            select(func.count()).select_from(Bot).where(Bot.state == BotState.RUNNING)
        )).scalar()
        stopped = (await s.execute(
            select(func.count()).select_from(Bot).where(Bot.state == BotState.STOPPED)
        )).scalar()
        crashed = (await s.execute(
            select(func.count()).select_from(Bot).where(Bot.state == BotState.CRASHED)
        )).scalar()

    text = (
        "🚀 <b>Shuvo Hosting Manager</b>\n\n"
        f"Welcome back, <b>Admin</b>.\n"
        f"Manage all your hosted Telegram bots directly from Telegram.\n\n"
        f"🤖 Hosted Bots: <b>{total}</b>\n"
        f"🟢 Running: <b>{running}</b>\n"
        f"🔴 Stopped: <b>{stopped}</b>\n"
        f"⚠️ Crashed: <b>{crashed}</b>\n\n"
        f"Choose an option below."
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu())


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    text = (
        "ℹ️ <b>Shuvo Hosting Bot — Commands</b>\n\n"
        "/start — Main menu\n"
        "/bots — Bot list\n"
        "/stats — Server stats\n"
        "/status — All bots status\n"
        "/restart &lt;name&gt; — Quick restart\n"
        "/stop &lt;name&gt; — Quick stop\n"
        "/start_bot &lt;name&gt; — Quick start\n"
        "/logs &lt;name&gt; — Quick log view\n"
        "/backup — Backup all bots\n"
        "/maintenance — Toggle maintenance mode\n"
        "/help — This help\n"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu())


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(Bot).order_by(Bot.name))
        bots   = result.scalars().all()

    if not bots:
        await update.message.reply_text("No bots hosted yet.", reply_markup=main_menu())
        return

    lines = ["📋 <b>All Bots Status</b>\n"]
    for b in bots:
        emoji = STATE_EMOJI.get(b.state, "❓")
        lines.append(f"{emoji} <b>{b.name}</b> — {b.state}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=main_menu())

"""
handlers/auto_update.py — Auto-update UI handlers.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, CallbackQueryHandler, filters,
)
from sqlalchemy import select

from database import AsyncSessionLocal, Bot
from keyboards.main import cancel_kb, main_menu
from services.github_api import parse_github_url
from services.auto_updater import (
    check_for_update, update_bot_from_github,
)
from utils.security import is_admin

logger = logging.getLogger(__name__)

WAIT_REPO_URL = 1
_auto_ctx: dict[int, dict] = {}


# ═══════════════════════════════════════════════════════════════════════════
# Toggle auto-update on/off
# ═══════════════════════════════════════════════════════════════════════════

async def cb_toggle_auto_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    bot_id = int(q.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if not bot:
            await q.answer("Bot not found", show_alert=True)
            return
        if not bot.github_url:
            await q.answer(
                "Set a GitHub repo first (🔗 Set Repo)",
                show_alert=True,
            )
            return
        bot.auto_update = not bot.auto_update
        new_state = bot.auto_update
        await s.commit()

    status = "✅ ON" if new_state else "⛔ OFF"
    await q.answer(f"Auto-update: {status}")

    # Refresh the message
    try:
        from handlers.git_handler import cb_bot_git
        await cb_bot_git(update, ctx)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# Set repo URL
# ═══════════════════════════════════════════════════════════════════════════

async def cb_set_repo_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    bot_id = int(q.data.split(":")[1])
    _auto_ctx[update.effective_user.id] = {"bot_id": bot_id}

    await q.message.reply_text(
        "🔗 <b>Set GitHub Repo</b>\n\n"
        "Send the repo URL:\n"
        "<code>https://github.com/user/repo</code>\n\n"
        "Optional branch: <code>https://github.com/user/repo#develop</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    return WAIT_REPO_URL


async def receive_repo_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    data = _auto_ctx.pop(user_id, None)
    if not data:
        await update.message.reply_text("Session expired.", reply_markup=main_menu())
        return ConversationHandler.END

    text = update.message.text.strip()
    branch = "main"
    if "#" in text:
        text, branch = text.rsplit("#", 1)
        branch = branch.strip() or "main"

    parsed = parse_github_url(text)
    if not parsed:
        await update.message.reply_text(
            "❌ Invalid GitHub URL. Try again or /cancel."
        )
        _auto_ctx[user_id] = data
        return WAIT_REPO_URL

    owner, repo = parsed
    bot_id = data["bot_id"]

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if not bot:
            await update.message.reply_text("Bot not found.", reply_markup=main_menu())
            return ConversationHandler.END
        bot.github_url = text
        bot.github_branch = branch
        # Reset SHA so next check will mark update available
        bot.last_commit_sha = None
        await s.commit()

    await update.message.reply_text(
        f"✅ Repo set:\n"
        f"<code>{owner}/{repo}</code>\n"
        f"branch: <code>{branch}</code>\n\n"
        f"💡 Now tap <b>🔄 Check now</b> to pull the latest code.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
# Manual check / pull now
# ═══════════════════════════════════════════════════════════════════════════

async def cb_check_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer("Checking GitHub…")
    bot_id = int(q.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        await q.message.reply_text("Bot not found.")
        return

    if not bot.github_url:
        await q.message.reply_text(
            "❌ No GitHub URL set. Use 🔗 Set Repo first."
        )
        return

    msg = await q.message.reply_text("⏳ Checking for updates…")

    # First just check
    has_update, info, new_sha = await check_for_update(bot)
    if not has_update:
        await msg.edit_text(f"ℹ️ {info}")
        return

    # Update available → confirm or auto-do it
    await msg.edit_text(f"⏳ {info}\n\nDownloading…")

    ok, result = await update_bot_from_github(bot_id)
    if ok:
        await msg.edit_text(f"✅ {result}")
    else:
        await msg.edit_text(f"❌ {result}")


async def cb_manual_pull(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias — same as check_now but different button label."""
    await cb_check_now(update, ctx)


# ═══════════════════════════════════════════════════════════════════════════
# Cancel
# ═══════════════════════════════════════════════════════════════════════════

async def au_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    _auto_ctx.pop(update.effective_user.id, None)
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
# Conversation handler
# ═══════════════════════════════════════════════════════════════════════════

def set_repo_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_set_repo_start, pattern=r"^set_repo:"),
        ],
        states={
            WAIT_REPO_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_repo_url),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), au_cancel),
            CommandHandler("cancel", au_cancel),
        ],
        per_message=False,
    )
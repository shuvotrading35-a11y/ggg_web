"""
handlers/env_editor.py — Raw .env editor (paste full content at once).
"""

from __future__ import annotations

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ContextTypes, ConversationHandler, CallbackQueryHandler,
    MessageHandler, CommandHandler, filters,
)

from database import AsyncSessionLocal, Bot
from utils.security import is_admin

WAIT_ENV_CONTENT = 1

_env_ctx: dict[int, dict] = {}


# ═══════════════════════════════════════════════════════════════════════════
# Trigger: user clicks "✏️ Raw Edit" button on the env view
# ═══════════════════════════════════════════════════════════════════════════

async def cb_env_raw_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    # callback data: env_edit_raw:<bot_id>
    try:
        bot_id = int(q.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await q.edit_message_text("❌ Invalid bot id.")
        return ConversationHandler.END

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)

    if not bot:
        await q.edit_message_text("❌ Bot not found.")
        return ConversationHandler.END

    env_path = Path(bot.directory) / ".env"
    current = ""
    if env_path.exists():
        try:
            current = env_path.read_text(errors="replace")
        except Exception as e:
            current = f"# read error: {e}"

    display = current if len(current) <= 3200 else "…(truncated)…\n" + current[-3200:]

    _env_ctx[update.effective_user.id] = {
        "bot_id": bot_id,
        "bot_name": bot.name,
        "env_path": str(env_path),
    }

    text = (
        f"✏️ <b>Raw .env Editor</b>\n\n"
        f"🤖 Bot: <b>{bot.name}</b>\n"
        f"📄 Path: <code>{env_path}</code>\n\n"
        f"<b>Current content:</b>\n"
        f"<pre>{display or '# (empty)'}</pre>\n\n"
        f"👇 এখন <b>পুরো নতুন .env content</b> একবারে পাঠান।\n\n"
        f"💡 প্রতিটা লাইন <code>KEY=value</code> format-এ।\n"
        f"বাতিল করতে /cancel"
    )

    await q.edit_message_text(text, parse_mode="HTML")
    return WAIT_ENV_CONTENT


# ═══════════════════════════════════════════════════════════════════════════
# Receive the full content and save
# ═══════════════════════════════════════════════════════════════════════════

async def receive_env_content(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    data = _env_ctx.get(user_id, {})

    if not data:
        await update.message.reply_text("❌ Session expired. আবার শুরু করুন।")
        return ConversationHandler.END

    content = update.message.text or ""

    # Save
    env_path = Path(data["env_path"])
    try:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(content.rstrip() + "\n")
    except Exception as e:
        await update.message.reply_text(
            f"❌ Save failed: <code>{e}</code>", parse_mode="HTML"
        )
        return ConversationHandler.END

    # Stats
    active_lines = [
        l for l in content.splitlines()
        if l.strip() and not l.strip().startswith("#") and "=" in l
    ]

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Restart bot", callback_data=f"bot_restart:{data['bot_id']}")],
        [InlineKeyboardButton("🔐 Env Vars",   callback_data=f"bot_env:{data['bot_id']}")],
        [InlineKeyboardButton("🤖 My Bots",    callback_data="bot_list")],
    ])

    await update.message.reply_text(
        f"✅ <b>.env saved!</b>\n\n"
        f"🤖 Bot: <b>{data['bot_name']}</b>\n"
        f"📄 Path: <code>{data['env_path']}</code>\n"
        f"📝 {len(active_lines)} active variable(s)\n\n"
        f"Restart করলে নতুন env ব্যবহার হবে।",
        parse_mode="HTML",
        reply_markup=kb,
    )

    _env_ctx.pop(user_id, None)
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
# Cancel
# ═══════════════════════════════════════════════════════════════════════════

async def cancel_env_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    _env_ctx.pop(update.effective_user.id, None)
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
# Conversation handler
# ═══════════════════════════════════════════════════════════════════════════

def env_raw_edit_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_env_raw_edit, pattern=r"^env_edit_raw:"),
        ],
        states={
            WAIT_ENV_CONTENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_env_content),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_env_edit),
        ],
        per_message=False,
    )
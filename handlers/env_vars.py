"""
handlers/env_vars.py — Per-bot environment variable management.
"""

from __future__ import annotations

from pathlib import Path

from telegram import Update
from telegram.ext import (
    CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, CallbackQueryHandler, filters
)

from database import AsyncSessionLocal, Bot, EnvVar
from keyboards.bots import env_kb
from keyboards.main import cancel_kb, main_menu
from utils.security import decrypt, encrypt, is_admin, mask
from sqlalchemy import select

# Conversation states
WAIT_KEY, WAIT_VALUE, WAIT_DEL_KEY = range(3)

_env_ctx: dict[int, dict] = {}


async def cb_bot_env(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        await query.edit_message_text("❌ Bot not found.")
        return

    await query.edit_message_text(
        f"⚙️ <b>Environment Variables — {bot.name}</b>",
        parse_mode="HTML",
        reply_markup=env_kb(bot_id),
    )


async def cb_env_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        result = await s.execute(select(EnvVar).where(EnvVar.bot_id == bot_id))
        vars_  = result.scalars().all()

    if not vars_:
        await query.edit_message_text(
            "No variables set.\n\nUse ➕ Add Variable to add one.",
            reply_markup=env_kb(bot_id),
        )
        return

    lines = ["⚙️ <b>Environment Variables</b>\n"]
    for v in vars_:
        try:
            val = decrypt(v.value_enc)
            masked = mask(val)
        except Exception:
            masked = "****"
        lines.append(f"<code>{v.key}</code> = <code>{masked}</code>")

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=env_kb(bot_id),
    )


async def cb_env_add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])
    user_id = update.effective_user.id

    _env_ctx[user_id] = {"bot_id": bot_id}
    await query.message.reply_text(
        "🔑 Enter the variable <b>key</b> (e.g. <code>BOT_TOKEN</code>):",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    return WAIT_KEY


async def env_receive_key(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    key     = update.message.text.strip().upper()
    if not key or " " in key:
        await update.message.reply_text("❌ Invalid key. No spaces allowed.")
        return WAIT_KEY
    _env_ctx[user_id]["key"] = key
    await update.message.reply_text(
        f"🔒 Enter the <b>value</b> for <code>{key}</code>:",
        parse_mode="HTML",
    )
    return WAIT_VALUE


async def env_receive_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    value   = update.message.text.strip()
    data    = _env_ctx.pop(user_id, {})
    bot_id  = data.get("bot_id")
    key     = data.get("key")

    if not bot_id or not key:
        await update.message.reply_text("❌ Session expired.", reply_markup=main_menu())
        return ConversationHandler.END

    enc = encrypt(value)

    async with AsyncSessionLocal() as s:
        existing = await s.execute(
            select(EnvVar).where(EnvVar.bot_id == bot_id, EnvVar.key == key)
        )
        obj = existing.scalars().first()
        if obj:
            obj.value_enc = enc
        else:
            s.add(EnvVar(bot_id=bot_id, key=key, value_enc=enc))
        await s.commit()

        # Rewrite .env file for the bot
        bot = await s.get(Bot, bot_id)
        if bot:
            await _rewrite_env_file(bot_id, Path(bot.directory))

    # Try to delete the message containing the token
    try:
        await update.message.delete()
    except Exception:
        pass

    await update.message.reply_text(
        f"✅ Variable <code>{key}</code> saved.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


async def _rewrite_env_file(bot_id: int, bot_dir: Path) -> None:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(EnvVar).where(EnvVar.bot_id == bot_id))
        vars_  = result.scalars().all()
    lines = []
    for v in vars_:
        try:
            val = decrypt(v.value_enc)
            lines.append(f"{v.key}={val}")
        except Exception:
            pass
    (bot_dir / ".env").write_text("\n".join(lines))


async def cb_env_del_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])
    user_id = update.effective_user.id

    _env_ctx[user_id] = {"bot_id": bot_id}
    await query.message.reply_text(
        "🗑 Enter the variable <b>key</b> to delete:",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    return WAIT_DEL_KEY


async def env_receive_del_key(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    key     = update.message.text.strip().upper()
    data    = _env_ctx.pop(user_id, {})
    bot_id  = data.get("bot_id")

    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(EnvVar).where(EnvVar.bot_id == bot_id, EnvVar.key == key)
        )
        obj = result.scalars().first()
        if not obj:
            await update.message.reply_text(f"❌ Variable <code>{key}</code> not found.", parse_mode="HTML")
            return ConversationHandler.END
        await s.delete(obj)
        await s.commit()

        bot = await s.get(Bot, bot_id)
        if bot:
            await _rewrite_env_file(bot_id, Path(bot.directory))

    await update.message.reply_text(f"🗑 Variable <code>{key}</code> deleted.", parse_mode="HTML", reply_markup=main_menu())
    return ConversationHandler.END


async def env_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    _env_ctx.pop(update.effective_user.id, None)
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu())
    return ConversationHandler.END


def env_add_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_env_add_start, pattern=r"^env_add:")],
        states={
            WAIT_KEY:   [MessageHandler(filters.TEXT & ~filters.COMMAND, env_receive_key)],
            WAIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, env_receive_value)],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), env_cancel),
            CommandHandler("cancel", env_cancel),
        ],
        per_message=False,
    )


def env_del_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_env_del_start, pattern=r"^env_del:")],
        states={
            WAIT_DEL_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, env_receive_del_key)],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), env_cancel),
            CommandHandler("cancel", env_cancel),
        ],
        per_message=False,
    )

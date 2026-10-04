"""
handlers/env_vars.py — Per-bot environment variable management.
Supports add / delete / view / RAW editor.
"""

from __future__ import annotations

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, CallbackQueryHandler, filters
)
from sqlalchemy import select

from database import AsyncSessionLocal, Bot, EnvVar
from keyboards.bots import env_kb
from keyboards.main import cancel_kb, main_menu
from utils.security import decrypt, encrypt, is_admin, mask

# Conversation states
WAIT_KEY, WAIT_VALUE, WAIT_DEL_KEY, WAIT_RAW_ENV = range(4)

_env_ctx: dict[int, dict] = {}


# ═══════════════════════════════════════════════════════════════════════════
# Menu / View
# ═══════════════════════════════════════════════════════════════════════════

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
            "No variables set.\n\nUse ➕ Add Variable or ✏️ Raw Editor.",
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


# ═══════════════════════════════════════════════════════════════════════════
# Add Variable
# ═══════════════════════════════════════════════════════════════════════════

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

        bot = await s.get(Bot, bot_id)
        if bot:
            await _rewrite_env_file(bot_id, Path(bot.directory))

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
    bot_dir.mkdir(parents=True, exist_ok=True)
    (bot_dir / ".env").write_text("\n".join(lines) + "\n")


# ═══════════════════════════════════════════════════════════════════════════
# Delete Variable
# ═══════════════════════════════════════════════════════════════════════════

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
            await update.message.reply_text(
                f"❌ Variable <code>{key}</code> not found.", parse_mode="HTML"
            )
            return ConversationHandler.END
        await s.delete(obj)
        await s.commit()

        bot = await s.get(Bot, bot_id)
        if bot:
            await _rewrite_env_file(bot_id, Path(bot.directory))

    await update.message.reply_text(
        f"🗑 Variable <code>{key}</code> deleted.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
# RAW Editor
# ═══════════════════════════════════════════════════════════════════════════

def _parse_env_text(text: str) -> dict[str, str]:
    """Parse KEY=value lines. Ignore comments/blank. Strip quotes."""
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key:
            result[key] = val
    return result


async def cb_env_raw_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point — show current .env and ask for the new full content."""
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        await query.edit_message_text("❌ Bot not found.")
        return ConversationHandler.END

    bot_dir = Path(bot.directory)
    env_path = bot_dir / ".env"

    # Read current
    current = ""
    if env_path.exists():
        try:
            current = env_path.read_text(errors="replace")
        except Exception:
            current = ""

    display = current if len(current) <= 3000 else "…(truncated)…\n" + current[-3000:]
    safe_display = (
        display.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        or "# (empty — no env vars yet)"
    )

    _env_ctx[update.effective_user.id] = {
        "bot_id": bot_id,
        "bot_name": bot.name,
        "bot_dir": str(bot_dir),
    }

    text = (
        f"✏️ <b>Raw .env Editor</b>\n\n"
        f"🤖 Bot: <b>{bot.name}</b>\n"
        f"📄 File: <code>{env_path}</code>\n\n"
        f"<b>📋 Current content:</b>\n"
        f"<pre>{safe_display}</pre>\n"
        f"👇 এখন <b>পুরো নতুন .env content</b> একবারে পাঠান।\n\n"
        f"📝 Format: প্রতিটা লাইন <code>KEY=value</code>\n"
        f"💬 Comment: <code>#</code> দিয়ে শুরু\n"
        f"❌ বাতিল করতে /cancel"
    )

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=cancel_kb())
    return WAIT_RAW_ENV


async def env_receive_raw(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive full .env content, save to DB + rewrite file."""
    user_id = update.effective_user.id
    data = _env_ctx.pop(user_id, None)

    if not data:
        await update.message.reply_text(
            "❌ Session expired. আবার শুরু করুন।", reply_markup=main_menu()
        )
        return ConversationHandler.END

    raw = update.message.text or ""
    parsed = _parse_env_text(raw)

    if not parsed:
        await update.message.reply_text(
            "⚠️ কোনো valid <code>KEY=value</code> line পাওয়া যায়নি।\n"
            "আবার পাঠান অথবা /cancel চাপুন।",
            parse_mode="HTML",
        )
        # restore ctx so user can retry
        _env_ctx[user_id] = data
        return WAIT_RAW_ENV

    bot_id = data["bot_id"]
    bot_dir = Path(data["bot_dir"])

    added = updated = deleted = 0
    try:
        async with AsyncSessionLocal() as s:
            result = await s.execute(select(EnvVar).where(EnvVar.bot_id == bot_id))
            existing = {v.key: v for v in result.scalars().all()}

            for key, val in parsed.items():
                if key in existing:
                    existing[key].value_enc = encrypt(val)
                    updated += 1
                else:
                    s.add(EnvVar(bot_id=bot_id, key=key, value_enc=encrypt(val)))
                    added += 1

            for key, obj in existing.items():
                if key not in parsed:
                    await s.delete(obj)
                    deleted += 1

            await s.commit()

        # Rewrite .env file
        bot_dir.mkdir(parents=True, exist_ok=True)
        lines = [f"{k}={v}" for k, v in parsed.items()]
        (bot_dir / ".env").write_text("\n".join(lines) + "\n")

    except Exception as e:
        await update.message.reply_text(
            f"❌ Save failed:\n<code>{e}</code>",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return ConversationHandler.END

    # Delete user's raw-env message (contains secrets)
    try:
        await update.message.delete()
    except Exception:
        pass

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Restart Bot", callback_data=f"bot_restart:{bot_id}")],
        [InlineKeyboardButton("👁 View Env",    callback_data=f"env_view:{bot_id}")],
        [InlineKeyboardButton("⚙️ Env Menu",    callback_data=f"bot_env:{bot_id}")],
        [InlineKeyboardButton("🤖 My Bots",     callback_data="bot_list")],
    ])

    summary = []
    if added:   summary.append(f"➕ {added} added")
    if updated: summary.append(f"✏️ {updated} updated")
    if deleted: summary.append(f"🗑 {deleted} deleted")
    if not summary:
        summary.append("(no changes)")

    await update.message.reply_text(
        f"✅ <b>.env saved successfully!</b>\n\n"
        f"🤖 Bot: <b>{data['bot_name']}</b>\n"
        f"📄 File: <code>{bot_dir}/.env</code>\n\n"
        f"<b>Changes:</b>\n" + "\n".join(summary) + "\n\n"
        f"⚠️ নতুন value apply করতে <b>Restart</b> করুন।",
        parse_mode="HTML",
        reply_markup=kb,
    )

    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
# Cancel
# ═══════════════════════════════════════════════════════════════════════════

async def env_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    _env_ctx.pop(update.effective_user.id, None)
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
# Conversation handlers
# ═══════════════════════════════════════════════════════════════════════════

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
            WAIT_DEL_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, env_receive_del_key)
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), env_cancel),
            CommandHandler("cancel", env_cancel),
        ],
        per_message=False,
    )


def env_raw_conversation() -> ConversationHandler:
    """Raw .env editor conversation."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_env_raw_start, pattern=r"^env_edit_raw:")
        ],
        states={
            WAIT_RAW_ENV: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, env_receive_raw)
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), env_cancel),
            CommandHandler("cancel", env_cancel),
        ],
        per_message=False,
    )
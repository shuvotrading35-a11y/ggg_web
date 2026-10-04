"""
handlers/env_editor.py — Raw .env editor.

Lets user paste full .env content at once. Parses it, updates the
encrypted DB entries (EnvVar) and rewrites the .env file.
"""

from __future__ import annotations

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, CallbackQueryHandler, filters,
)
from sqlalchemy import select

from database import AsyncSessionLocal, Bot, EnvVar
from keyboards.main import cancel_kb, main_menu
from utils.security import decrypt, encrypt, is_admin

WAIT_ENV_CONTENT = 1

_env_edit_ctx: dict[int, dict] = {}


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
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

        # Strip surrounding quotes
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]

        if key:
            result[key] = val
    return result


def _read_current_env(bot_dir: Path) -> str:
    p = bot_dir / ".env"
    if p.exists():
        try:
            return p.read_text(errors="replace")
        except Exception:
            return ""
    return ""


async def _save_env_to_db(bot_id: int, data: dict[str, str]) -> tuple[int, int, int]:
    """
    Returns (added, updated, deleted) counts.
    """
    added = updated = deleted = 0

    async with AsyncSessionLocal() as s:
        # Fetch existing
        result = await s.execute(select(EnvVar).where(EnvVar.bot_id == bot_id))
        existing = {v.key: v for v in result.scalars().all()}

        # Upsert
        for key, val in data.items():
            if key in existing:
                existing[key].value_enc = encrypt(val)
                updated += 1
            else:
                s.add(EnvVar(bot_id=bot_id, key=key, value_enc=encrypt(val)))
                added += 1

        # Delete removed keys
        for key, obj in existing.items():
            if key not in data:
                await s.delete(obj)
                deleted += 1

        await s.commit()

    return added, updated, deleted


async def _write_env_file(bot_dir: Path, data: dict[str, str]) -> None:
    bot_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in data.items()]
    (bot_dir / ".env").write_text("\n".join(lines) + "\n")


# ═══════════════════════════════════════════════════════════════════════════
# Trigger
# ═══════════════════════════════════════════════════════════════════════════

async def cb_env_edit_raw(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    try:
        bot_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Invalid bot id.")
        return ConversationHandler.END

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)

    if not bot:
        await query.edit_message_text("❌ Bot not found.")
        return ConversationHandler.END

    bot_dir = Path(bot.directory)
    current = _read_current_env(bot_dir)

    # Show last 3000 chars if too big
    if len(current) > 3000:
        display = "…(truncated)…\n" + current[-3000:]
    else:
        display = current or "# (empty — no env vars yet)"

    _env_edit_ctx[update.effective_user.id] = {
        "bot_id": bot_id,
        "bot_name": bot.name,
        "bot_dir": str(bot_dir),
    }

    # Escape for HTML <pre>
    safe_display = display.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    text = (
        f"✏️ <b>Raw .env Editor</b>\n\n"
        f"🤖 Bot: <b>{bot.name}</b>\n"
        f"📄 File: <code>{bot_dir}/.env</code>\n\n"
        f"<b>📋 Current content:</b>\n"
        f"<pre>{safe_display}</pre>\n"
        f"👇 এখন <b>পুরো নতুন .env content</b> একবারে পাঠান।\n\n"
        f"📝 Format: প্রতিটা লাইন <code>KEY=value</code>\n"
        f"💬 Comment: <code>#</code> দিয়ে শুরু\n"
        f"❌ বাতিল করতে /cancel"
    )

    await query.edit_message_text(text, parse_mode="HTML")
    return WAIT_ENV_CONTENT


# ═══════════════════════════════════════════════════════════════════════════
# Receive content
# ═══════════════════════════════════════════════════════════════════════════

async def receive_env_content(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    data = _env_edit_ctx.get(user_id)

    if not data:
        await update.message.reply_text(
            "❌ Session expired. আবার শুরু করুন।",
            reply_markup=main_menu(),
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
        return WAIT_ENV_CONTENT

    bot_id = data["bot_id"]
    bot_dir = Path(data["bot_dir"])

    # Save to DB + write .env file
    try:
        added, updated, deleted = await _save_env_to_db(bot_id, parsed)
        await _write_env_file(bot_dir, parsed)
    except Exception as e:
        await update.message.reply_text(
            f"❌ Save failed:\n<code>{e}</code>",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        _env_edit_ctx.pop(user_id, None)
        return ConversationHandler.END

    _env_edit_ctx.pop(user_id, None)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Restart Bot",  callback_data=f"bot_restart:{bot_id}")],
        [InlineKeyboardButton("👁 View Env",     callback_data=f"env_view:{bot_id}")],
        [InlineKeyboardButton("⚙️ Env Menu",     callback_data=f"bot_env:{bot_id}")],
        [InlineKeyboardButton("🤖 My Bots",      callback_data="bot_list")],
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

async def env_edit_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    _env_edit_ctx.pop(update.effective_user.id, None)
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
# Conversation handler
# ═══════════════════════════════════════════════════════════════════════════

def env_raw_edit_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_env_edit_raw, pattern=r"^env_edit_raw:"),
        ],
        states={
            WAIT_ENV_CONTENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_env_content),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), env_edit_cancel),
            CommandHandler("cancel", env_edit_cancel),
        ],
        per_message=False,
    )
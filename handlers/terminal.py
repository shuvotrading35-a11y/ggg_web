"""
handlers/terminal.py — Safe terminal emulator via Telegram.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)

from config import TERMINAL_ALLOWED
from keyboards.main import cancel_kb, main_menu
from services.audit import log_action
from services.terminal import TerminalDenied, is_allowed, run_command
from utils.security import is_admin

WAIT_CMD = 0
_term_ctx: dict[int, dict] = {}


async def show_terminal(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return

    allowed_str = " ".join(f"<code>{c}</code>" for c in sorted(TERMINAL_ALLOWED))
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💻 Open Terminal", callback_data="term_open"),
    ]])
    await update.message.reply_text(
        "💻 <b>Terminal</b>\n\n"
        f"Allowed commands:\n{allowed_str}",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def cb_term_open(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if not is_admin(user_id):
        return ConversationHandler.END

    _term_ctx[user_id] = {}
    await query.message.reply_text(
        "💻 <b>Terminal Ready</b>\n\n"
        "Send a command to execute on the VPS.\n"
        "Example: <code>df -h</code>  |  <code>ps aux</code>  |  <code>free -m</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    return WAIT_CMD


async def term_receive_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    command = update.message.text.strip()

    if not command:
        await update.message.reply_text("⚠️ Empty command.")
        return WAIT_CMD

    msg = await update.message.reply_text(f"⏳ Running: <code>{command}</code>", parse_mode="HTML")

    try:
        ok, output = await run_command(command)
        status = "✅" if ok else "❌"
        result_text = (
            f"{status} <code>{command}</code>\n\n"
            f"<pre>{output[:3800]}</pre>"
        )
        if len(output) > 3800:
            result_text += "\n<i>…output truncated</i>"

        await msg.edit_text(result_text, parse_mode="HTML")
        await log_action(f"Terminal: {command}")

    except TerminalDenied as e:
        await msg.edit_text(str(e), parse_mode="HTML")

    # Quick action buttons for next command
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔁 Run Another", callback_data="term_open"),
        InlineKeyboardButton("❌ Close",        callback_data="term_close"),
    ]])
    await update.message.reply_text("Next action:", reply_markup=kb)
    return WAIT_CMD


async def cb_term_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    _term_ctx.pop(user_id, None)
    await query.edit_message_text("💻 Terminal closed.")
    return ConversationHandler.END


async def term_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    _term_ctx.pop(update.effective_user.id, None)
    await update.message.reply_text("❌ Terminal closed.", reply_markup=main_menu())
    return ConversationHandler.END


def terminal_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_term_open, pattern="^term_open$")],
        states={
            WAIT_CMD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, term_receive_cmd),
                CallbackQueryHandler(cb_term_open,  pattern="^term_open$"),
                CallbackQueryHandler(cb_term_close, pattern="^term_close$"),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), term_cancel),
            CommandHandler("cancel", term_cancel),
        ],
        per_message=False,
    )

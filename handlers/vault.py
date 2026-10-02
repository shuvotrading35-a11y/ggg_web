"""
handlers/vault.py — Secure Token Vault management.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, CallbackQueryHandler, filters
)

from keyboards.main import cancel_kb, main_menu
from services.token_vault import vault_add, vault_delete, vault_list
from utils.security import is_admin

WAIT_LABEL, WAIT_TOKEN_VALUE, WAIT_DEL_LABEL = range(3)
_ctx: dict[int, dict] = {}


async def show_vault(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return

    items = await vault_list()
    if not items:
        text = "🔐 <b>Token Vault</b>\n\nNo tokens stored yet."
    else:
        lines = ["🔐 <b>Token Vault</b>\n"]
        for item in items:
            lines.append(f"• <code>{item['label']}</code> → <code>{item['masked']}</code>")
        text = "\n".join(lines)

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Token",    callback_data="vault_add"),
            InlineKeyboardButton("🗑 Delete Token", callback_data="vault_del"),
        ]
    ])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def cb_vault_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    _ctx[user_id] = {}
    await query.message.reply_text(
        "🏷 Enter a <b>label</b> for this token (e.g. <code>BOT_TOKEN_PAYMENT</code>):",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    return WAIT_LABEL


async def vault_receive_label(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    label   = update.message.text.strip().upper()
    if not label:
        await update.message.reply_text("❌ Invalid label.")
        return WAIT_LABEL
    _ctx[user_id]["label"] = label
    await update.message.reply_text(
        f"🔒 Enter the <b>token value</b> for <code>{label}</code>:",
        parse_mode="HTML",
    )
    return WAIT_TOKEN_VALUE


async def vault_receive_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    value   = update.message.text.strip()
    data    = _ctx.pop(user_id, {})
    label   = data.get("label", "")

    await vault_add(label, value)

    try:
        await update.message.delete()
    except Exception:
        pass

    await update.message.reply_text(
        f"✅ Token <code>{label}</code> saved to vault.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


async def cb_vault_del(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    _ctx[user_id] = {}
    await query.message.reply_text(
        "🗑 Enter the <b>label</b> to delete from vault:",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    return WAIT_DEL_LABEL


async def vault_receive_del_label(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    label   = update.message.text.strip().upper()
    _ctx.pop(user_id, None)

    ok = await vault_delete(label)
    if ok:
        await update.message.reply_text(f"🗑 Token <code>{label}</code> deleted.", parse_mode="HTML", reply_markup=main_menu())
    else:
        await update.message.reply_text(f"❌ Token <code>{label}</code> not found.", parse_mode="HTML", reply_markup=main_menu())
    return ConversationHandler.END


async def vault_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    _ctx.pop(update.effective_user.id, None)
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu())
    return ConversationHandler.END


def vault_add_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_vault_add, pattern="^vault_add$")],
        states={
            WAIT_LABEL:       [MessageHandler(filters.TEXT & ~filters.COMMAND, vault_receive_label)],
            WAIT_TOKEN_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, vault_receive_value)],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), vault_cancel),
            CommandHandler("cancel", vault_cancel),
        ],
        per_message=False,
    )


def vault_del_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_vault_del, pattern="^vault_del$")],
        states={
            WAIT_DEL_LABEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, vault_receive_del_label)],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), vault_cancel),
            CommandHandler("cancel", vault_cancel),
        ],
        per_message=False,
    )

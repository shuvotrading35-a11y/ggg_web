"""
handlers/upload.py — Handle .py and .zip uploads; auto-detect entry point.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, CommandHandler, filters, CallbackQueryHandler

from config import MAX_UPLOAD_BYTES, MAX_ZIP_BYTES, BotState, ALLOWED_EXTENSIONS
from database import AsyncSessionLocal, Bot
from keyboards.main import cancel_kb, main_menu
from services.file_manager import (
    bot_directory, detect_entry_points, detect_requirements,
    extract_zip_bot, save_single_py
)
from services.installer import install_requirements
from utils.security import is_admin, safe_slug

# Conversation states
WAIT_FILE, WAIT_NAME, WAIT_ENTRY = range(3)

_upload_ctx: dict[int, dict] = {}   # user_id → temp state


async def ask_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    await update.message.reply_text(
        "📦 <b>Add Bot</b>\n\n"
        "Send your Python bot file.\n\n"
        "Supported: <code>.py</code>  |  <code>.zip</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    return WAIT_FILE


async def receive_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return ConversationHandler.END

    doc = update.message.document
    if not doc:
        await update.message.reply_text("⚠️ Please send a file.")
        return WAIT_FILE

    filename = doc.file_name or "upload"
    suffix   = Path(filename).suffix.lower()

    if suffix not in ALLOWED_EXTENSIONS:
        await update.message.reply_text(f"❌ Only .py and .zip files are supported.")
        return WAIT_FILE

    size_limit = MAX_ZIP_BYTES if suffix == ".zip" else MAX_UPLOAD_BYTES
    if doc.file_size and doc.file_size > size_limit:
        mb = size_limit // (1024 * 1024)
        await update.message.reply_text(f"❌ File too large. Max {mb} MB.")
        return WAIT_FILE

    msg = await update.message.reply_text("⏳ Downloading file...")
    tg_file = await doc.get_file()

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        await tg_file.download_to_drive(tmp.name)
        tmp_path = Path(tmp.name)

    _upload_ctx[user_id] = {"tmp_path": tmp_path, "suffix": suffix, "filename": filename}
    await msg.edit_text("✅ File received!\n\n📝 What should this bot be named?")
    return WAIT_NAME


async def receive_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    name    = update.message.text.strip()

    if not name or len(name) > 100:
        await update.message.reply_text("⚠️ Invalid name. Try again.")
        return WAIT_NAME

    slug = safe_slug(name)

    # Check for duplicate
    async with AsyncSessionLocal() as s:
        from sqlalchemy import select
        existing = await s.execute(select(Bot).where(Bot.slug == slug))
        if existing.scalars().first():
            await update.message.reply_text(f"❌ A bot named '{name}' already exists. Choose a different name.")
            return WAIT_NAME

    _upload_ctx[user_id]["name"] = name
    _upload_ctx[user_id]["slug"] = slug

    ctx_data = _upload_ctx[user_id]
    suffix   = ctx_data["suffix"]
    tmp_path = ctx_data["tmp_path"]

    msg = await update.message.reply_text("⏳ Processing files...")

    if suffix == ".py":
        content = tmp_path.read_bytes()
        await save_single_py(ctx_data["filename"], content, slug)
        _upload_ctx[user_id]["entry"] = Path(ctx_data["filename"]).name
        tmp_path.unlink(missing_ok=True)
        await msg.edit_text(f"✅ File saved as <code>{Path(ctx_data['filename']).name}</code>", parse_mode="HTML")
        return await _finish_upload(update, ctx, user_id)

    else:  # .zip
        bot_dir, py_files = await extract_zip_bot(tmp_path, slug)
        tmp_path.unlink(missing_ok=True)
        _upload_ctx[user_id]["bot_dir"] = str(bot_dir)

        if not py_files:
            await msg.edit_text("❌ No .py files found in ZIP.")
            _cleanup(user_id)
            return ConversationHandler.END

        if len(py_files) == 1:
            _upload_ctx[user_id]["entry"] = py_files[0]
            await msg.edit_text(f"✅ ZIP extracted. Auto-detected entry: <code>{py_files[0]}</code>", parse_mode="HTML")
            return await _finish_upload(update, ctx, user_id)

        # Multiple py files — ask to select
        buttons = [
            [InlineKeyboardButton(f, callback_data=f"entry_select:{user_id}:{f}")]
            for f in py_files
        ]
        await msg.edit_text(
            "📂 Multiple Python files found.\nSelect the <b>main entry point</b>:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return WAIT_ENTRY


async def receive_entry_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query    = update.callback_query
    await query.answer()
    _, uid, entry = query.data.split(":", 2)
    user_id  = int(uid)

    if user_id != update.effective_user.id:
        return WAIT_ENTRY

    _upload_ctx[user_id]["entry"] = entry
    await query.edit_message_text(f"✅ Entry point selected: <code>{entry}</code>", parse_mode="HTML")
    return await _finish_upload(update, ctx, user_id)


async def _finish_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE, user_id: int) -> int:
    data     = _upload_ctx.get(user_id, {})
    name     = data.get("name", "Bot")
    slug     = data.get("slug", safe_slug(name))
    entry    = data.get("entry")
    bot_dir  = str(bot_directory(slug))

    msg_obj = update.message or (update.callback_query.message if update.callback_query else None)

    # Persist to DB
    async with AsyncSessionLocal() as s:
        bot = Bot(
            name=name, slug=slug,
            directory=bot_dir,
            entry_file=entry,
            state=BotState.STOPPED,
        )
        s.add(bot)
        await s.commit()
        bot_id = bot.id

    # Audit
    from services.audit import log_action
    await log_action(f"Uploaded bot: {name}", bot_name=name)

    # Install requirements if present
    has_req = detect_requirements(Path(bot_dir))
    if has_req:
        notice = await msg_obj.reply_text("⏳ Installing dependencies...")

        async def progress(m: str):
            try:
                await notice.edit_text(m)
            except Exception:
                pass

        ok, result_msg = await install_requirements(bot_id, progress)
        await notice.edit_text(result_msg, parse_mode="HTML")
    else:
        await msg_obj.reply_text(
            f"✅ <b>Bot added successfully!</b>\n\n"
            f"🤖 Name: <b>{name}</b>\n"
            f"📄 Entry: <code>{entry}</code>\n\n"
            f"Use '🤖 My Bots' to start it.",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )

    _cleanup(user_id)
    return ConversationHandler.END


async def cancel_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    _cleanup(user_id)
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu())
    return ConversationHandler.END


def _cleanup(user_id: int) -> None:
    data = _upload_ctx.pop(user_id, {})
    tmp  = data.get("tmp_path")
    if tmp:
        Path(tmp).unlink(missing_ok=True)


def upload_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Add Bot$"), ask_upload)],
        states={
            WAIT_FILE:  [MessageHandler(filters.Document.ALL, receive_file)],
            WAIT_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)],
            WAIT_ENTRY: [CallbackQueryHandler(receive_entry_callback, pattern=r"^entry_select:")],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), cancel_upload),
            CommandHandler("cancel", cancel_upload),
        ],
        per_message=False,
    )

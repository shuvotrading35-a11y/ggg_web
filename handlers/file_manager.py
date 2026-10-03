"""
handlers/file_manager.py — Telegram File Manager for hosted bots.
Browse folders, upload, download, delete, rename, zip folder.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)

from database import AsyncSessionLocal, Bot
from keyboards.main import cancel_kb, main_menu
from utils.security import is_admin, safe_filename

# Conversation states
WAIT_UPLOAD_FILE, WAIT_RENAME_NAME, WAIT_NEW_FOLDER_NAME = range(3)

# Supported file types for upload
SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".zip", ".json", ".txt", ".env",
    ".yml", ".yaml", ".cfg", ".ini", ".toml", ".md",
    ".sh", ".html", ".css",
}

# File type emoji mapping
FILE_EMOJI = {
    ".py":   "🐍",
    ".js":   "📜",
    ".zip":  "📦",
    ".json": "📋",
    ".txt":  "📄",
    ".env":  "🔑",
    ".yml":  "⚙️",
    ".yaml": "⚙️",
    ".cfg":  "⚙️",
    ".ini":  "⚙️",
    ".toml": "⚙️",
    ".md":   "📝",
    ".sh":   "⚡",
    ".html": "🌐",
    ".css":  "🎨",
    ".log":  "📜",
}

# Per-user context: stores current path and bot_id
_fm_ctx: dict[int, dict] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_file_emoji(name: str) -> str:
    ext = Path(name).suffix.lower()
    return FILE_EMOJI.get(ext, "📄")


def _fmt_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024*1024):.1f} MB"


def _safe_path(bot_dir: str, rel_path: str) -> Path | None:
    """Resolve path and ensure it stays within bot_dir (path traversal protection)."""
    base   = Path(bot_dir).resolve()
    target = (base / rel_path).resolve()
    if str(target).startswith(str(base)):
        return target
    return None


def _rel(bot_dir: str, path: Path) -> str:
    """Return relative path string from bot_dir."""
    return str(path.relative_to(Path(bot_dir).resolve()))


def _build_browser_kb(bot_id: int, bot_dir: str, current_path: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for directory listing."""
    base    = Path(bot_dir).resolve()
    target  = _safe_path(bot_dir, current_path)
    if not target or not target.is_dir():
        target = base

    rows = []

    # Header: current path
    rel = _rel(bot_dir, target)
    display_path = "/" if rel == "." else f"/{rel}"

    # List dirs first, then files
    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        entries = []

    for entry in entries:
        # Skip hidden system dirs
        if entry.name in ("venv", "__pycache__", ".git"):
            continue

        rel_entry = _rel(bot_dir, entry)

        if entry.is_dir():
            rows.append([InlineKeyboardButton(
                f"📁 {entry.name}/",
                callback_data=f"fm_cd:{bot_id}:{rel_entry}",
            )])
        else:
            size  = _fmt_size(entry.stat().st_size)
            emoji = _get_file_emoji(entry.name)
            rows.append([
                InlineKeyboardButton(
                    f"{emoji} {entry.name} ({size})",
                    callback_data=f"fm_file:{bot_id}:{rel_entry}",
                ),
            ])

    # Action buttons
    rows.append([
        InlineKeyboardButton("📤 Upload here", callback_data=f"fm_upload:{bot_id}:{_rel(bot_dir, target)}"),
        InlineKeyboardButton("📁 New Folder",  callback_data=f"fm_mkdir:{bot_id}:{_rel(bot_dir, target)}"),
    ])
    rows.append([
        InlineKeyboardButton("🔍 Search",    callback_data=f"fm_search:{bot_id}"),
        InlineKeyboardButton("⭐ Zip Folder", callback_data=f"fm_zipdir:{bot_id}:{_rel(bot_dir, target)}"),
    ])

    # Back navigation
    parent = target.parent
    if target != base:
        parent_rel = _rel(bot_dir, parent)
        rows.append([InlineKeyboardButton(
            "⬅️ Back to Parent",
            callback_data=f"fm_cd:{bot_id}:{parent_rel}",
        )])

    rows.append([InlineKeyboardButton("🔙 Back to Bot", callback_data=f"bot_detail:{bot_id}")])

    return InlineKeyboardMarkup(rows), display_path


def _build_file_kb(bot_id: int, rel_path: str) -> InlineKeyboardMarkup:
    """Keyboard for a single file."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📥 Download",  callback_data=f"fm_download:{bot_id}:{rel_path}"),
            InlineKeyboardButton("✏️ Rename",    callback_data=f"fm_rename:{bot_id}:{rel_path}"),
            InlineKeyboardButton("🗑 Delete",    callback_data=f"fm_del_confirm:{bot_id}:{rel_path}"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"fm_cd:{bot_id}:{str(Path(rel_path).parent)}")],
    ])


# ── Entry point ───────────────────────────────────────────────────────────────

async def cb_open_file_manager(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        await query.edit_message_text("❌ Bot not found.")
        return

    user_id = update.effective_user.id
    _fm_ctx[user_id] = {"bot_id": bot_id, "bot_dir": bot.directory, "current": "."}

    kb, display_path = _build_browser_kb(bot_id, bot.directory, ".")
    await query.edit_message_text(
        f"📁 <b>FILE MANAGER</b> — {bot.name}\n\n"
        f"📂 {display_path} (root)",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ── Browse directory ──────────────────────────────────────────────────────────

async def cb_fm_cd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    await query.answer()

    parts   = query.data.split(":", 3)
    bot_id  = int(parts[1])
    rel     = parts[2] if len(parts) > 2 else "."

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        return

    user_id = update.effective_user.id
    _fm_ctx[user_id] = {"bot_id": bot_id, "bot_dir": bot.directory, "current": rel}

    kb, display_path = _build_browser_kb(bot_id, bot.directory, rel)
    await query.edit_message_text(
        f"📁 <b>FILE MANAGER</b> — {bot.name}\n\n"
        f"📂 {display_path}",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ── File actions ──────────────────────────────────────────────────────────────

async def cb_fm_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show file action buttons."""
    query  = update.callback_query
    await query.answer()

    parts    = query.data.split(":", 3)
    bot_id   = int(parts[1])
    rel_path = parts[2]

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        return

    target = _safe_path(bot.directory, rel_path)
    if not target or not target.is_file():
        await query.edit_message_text("❌ File not found.")
        return

    size  = _fmt_size(target.stat().st_size)
    emoji = _get_file_emoji(target.name)

    await query.edit_message_text(
        f"{emoji} <b>{target.name}</b>\n\n"
        f"📂 Path: <code>/{rel_path}</code>\n"
        f"💾 Size: {size}",
        parse_mode="HTML",
        reply_markup=_build_file_kb(bot_id, rel_path),
    )


async def cb_fm_download(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Sending file…")

    parts    = query.data.split(":", 3)
    bot_id   = int(parts[1])
    rel_path = parts[2]

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        return

    target = _safe_path(bot.directory, rel_path)
    if not target or not target.is_file():
        await query.message.reply_text("❌ File not found.")
        return

    await query.message.reply_document(
        document=open(target, "rb"),
        filename=target.name,
    )


async def cb_fm_del_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()

    parts    = query.data.split(":", 3)
    bot_id   = int(parts[1])
    rel_path = parts[2]
    fname    = Path(rel_path).name

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancel",  callback_data=f"fm_file:{bot_id}:{rel_path}"),
        InlineKeyboardButton("🗑 Confirm", callback_data=f"fm_delete:{bot_id}:{rel_path}"),
    ]])
    await query.edit_message_text(
        f"⚠️ Delete <b>{fname}</b>?\n\nThis cannot be undone.",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def cb_fm_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Deleting…")

    parts    = query.data.split(":", 3)
    bot_id   = int(parts[1])
    rel_path = parts[2]

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        return

    target = _safe_path(bot.directory, rel_path)
    if not target:
        await query.edit_message_text("❌ Invalid path.")
        return

    try:
        if target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        await query.edit_message_text(f"🗑 <b>{target.name}</b> deleted.", parse_mode="HTML")
    except Exception as e:
        await query.edit_message_text(f"❌ Error: {e}")

    # Navigate back to parent
    parent_rel = _rel(bot.directory, target.parent)
    kb, display_path = _build_browser_kb(bot_id, bot.directory, parent_rel)
    await query.message.reply_text(
        f"📁 <b>FILE MANAGER</b> — {bot.name}\n\n📂 {display_path}",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ── Zip folder ────────────────────────────────────────────────────────────────

async def cb_fm_zipdir(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Zipping folder…")

    parts    = query.data.split(":", 3)
    bot_id   = int(parts[1])
    rel_path = parts[2]

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        return

    target = _safe_path(bot.directory, rel_path)
    if not target or not target.is_dir():
        await query.message.reply_text("❌ Directory not found.")
        return

    zip_name   = f"{target.name}_export.zip"
    zip_output = Path(bot.directory) / zip_name

    with zipfile.ZipFile(zip_output, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in target.rglob("*"):
            if any(part in ("venv", "__pycache__") for part in f.parts):
                continue
            if f.is_file():
                zf.write(f, f.relative_to(target))

    await query.message.reply_document(
        document=open(zip_output, "rb"),
        filename=zip_name,
    )
    zip_output.unlink(missing_ok=True)


# ── New Folder ────────────────────────────────────────────────────────────────

async def cb_fm_mkdir_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query  = update.callback_query
    await query.answer()

    parts    = query.data.split(":", 3)
    bot_id   = int(parts[1])
    rel_path = parts[2]
    user_id  = update.effective_user.id

    _fm_ctx.setdefault(user_id, {})
    _fm_ctx[user_id].update({"bot_id": bot_id, "mkdir_in": rel_path})

    await query.message.reply_text(
        "📁 Enter the <b>folder name</b>:",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    return WAIT_NEW_FOLDER_NAME


async def fm_receive_folder_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id  = update.effective_user.id
    data     = _fm_ctx.get(user_id, {})
    bot_id   = data.get("bot_id")
    mkdir_in = data.get("mkdir_in", ".")

    name = safe_filename(update.message.text.strip())
    if not name:
        await update.message.reply_text("❌ Invalid name.")
        return WAIT_NEW_FOLDER_NAME

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        return ConversationHandler.END

    target = _safe_path(bot.directory, mkdir_in)
    if not target:
        await update.message.reply_text("❌ Invalid path.")
        return ConversationHandler.END

    new_dir = target / name
    try:
        new_dir.mkdir(parents=True, exist_ok=True)
        await update.message.reply_text(f"✅ Folder <code>{name}</code> created.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

    # Show updated directory
    kb, display_path = _build_browser_kb(bot_id, bot.directory, mkdir_in)
    await update.message.reply_text(
        f"📁 <b>FILE MANAGER</b> — {bot.name}\n\n📂 {display_path}",
        parse_mode="HTML",
        reply_markup=kb,
    )
    return ConversationHandler.END


# ── Rename ────────────────────────────────────────────────────────────────────

async def cb_fm_rename_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query  = update.callback_query
    await query.answer()

    parts    = query.data.split(":", 3)
    bot_id   = int(parts[1])
    rel_path = parts[2]
    user_id  = update.effective_user.id

    _fm_ctx.setdefault(user_id, {})
    _fm_ctx[user_id].update({"bot_id": bot_id, "rename_path": rel_path})

    old_name = Path(rel_path).name
    await query.message.reply_text(
        f"✏️ Rename <code>{old_name}</code>\n\nEnter the new name:",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    return WAIT_RENAME_NAME


async def fm_receive_rename(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id  = update.effective_user.id
    data     = _fm_ctx.get(user_id, {})
    bot_id   = data.get("bot_id")
    rel_path = data.get("rename_path", "")

    new_name = safe_filename(update.message.text.strip())
    if not new_name:
        await update.message.reply_text("❌ Invalid name.")
        return WAIT_RENAME_NAME

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        return ConversationHandler.END

    target = _safe_path(bot.directory, rel_path)
    if not target or not target.exists():
        await update.message.reply_text("❌ File not found.")
        return ConversationHandler.END

    new_target = target.parent / new_name
    try:
        target.rename(new_target)
        await update.message.reply_text(
            f"✅ Renamed to <code>{new_name}</code>", parse_mode="HTML", reply_markup=main_menu()
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

    parent_rel = _rel(bot.directory, target.parent)
    kb, display_path = _build_browser_kb(bot_id, bot.directory, parent_rel)
    await update.message.reply_text(
        f"📁 <b>FILE MANAGER</b> — {bot.name}\n\n📂 {display_path}",
        parse_mode="HTML",
        reply_markup=kb,
    )
    return ConversationHandler.END


# ── Upload file ───────────────────────────────────────────────────────────────

async def cb_fm_upload_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query  = update.callback_query
    await query.answer()

    parts    = query.data.split(":", 3)
    bot_id   = int(parts[1])
    rel_path = parts[2]
    user_id  = update.effective_user.id

    _fm_ctx.setdefault(user_id, {})
    _fm_ctx[user_id].update({"bot_id": bot_id, "upload_to": rel_path})

    # List supported types
    ext_list = ", ".join(sorted(SUPPORTED_EXTENSIONS))
    await query.message.reply_text(
        f"📤 <b>Upload here</b>: <code>/{rel_path}</code>\n\n"
        f"Supported: <code>{ext_list}</code>\n\n"
        f"Send the file now:",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    return WAIT_UPLOAD_FILE


async def fm_receive_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    data    = _fm_ctx.get(user_id, {})
    bot_id  = data.get("bot_id")
    dest_rel = data.get("upload_to", ".")

    doc = update.message.document
    if not doc:
        await update.message.reply_text("⚠️ Please send a file.")
        return WAIT_UPLOAD_FILE

    filename = doc.file_name or "file"
    ext      = Path(filename).suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        await update.message.reply_text(
            f"❌ Unsupported file: <code>{ext}</code>\n\n"
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            parse_mode="HTML",
        )
        return WAIT_UPLOAD_FILE

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        return ConversationHandler.END

    dest_dir = _safe_path(bot.directory, dest_rel)
    if not dest_dir:
        await update.message.reply_text("❌ Invalid destination.")
        return ConversationHandler.END

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / safe_filename(filename)

    tg_file = await doc.get_file()
    await tg_file.download_to_drive(str(dest_file))

    size = _fmt_size(dest_file.stat().st_size)
    await update.message.reply_text(
        f"✅ <b>{dest_file.name}</b> uploaded!\n💾 Size: {size}",
        parse_mode="HTML",
    )

    kb, display_path = _build_browser_kb(bot_id, bot.directory, dest_rel)
    await update.message.reply_text(
        f"📁 <b>FILE MANAGER</b> — {bot.name}\n\n📂 {display_path}",
        parse_mode="HTML",
        reply_markup=kb,
    )
    return ConversationHandler.END


# ── Search ────────────────────────────────────────────────────────────────────

async def cb_fm_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    parts  = query.data.split(":", 2)
    bot_id = int(parts[1])

    # Store bot_id and trigger search via text (simple inline)
    user_id = update.effective_user.id
    _fm_ctx.setdefault(user_id, {})
    _fm_ctx[user_id]["bot_id"] = bot_id
    _fm_ctx[user_id]["searching"] = True

    await query.message.reply_text(
        "🔍 Enter filename or keyword to search:",
        reply_markup=cancel_kb(),
    )


async def fm_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    _fm_ctx.pop(user_id, None)
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu())
    return ConversationHandler.END


# ── Conversation handlers ─────────────────────────────────────────────────────

def fm_upload_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_fm_upload_start, pattern=r"^fm_upload:")],
        states={
            WAIT_UPLOAD_FILE: [MessageHandler(filters.Document.ALL, fm_receive_upload)],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), fm_cancel),
            CommandHandler("cancel", fm_cancel),
        ],
        per_message=False,
    )


def fm_mkdir_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_fm_mkdir_start, pattern=r"^fm_mkdir:")],
        states={
            WAIT_NEW_FOLDER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, fm_receive_folder_name)],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), fm_cancel),
            CommandHandler("cancel", fm_cancel),
        ],
        per_message=False,
    )


def fm_rename_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_fm_rename_start, pattern=r"^fm_rename:")],
        states={
            WAIT_RENAME_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, fm_receive_rename)],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), fm_cancel),
            CommandHandler("cancel", fm_cancel),
        ],
        per_message=False,
    )
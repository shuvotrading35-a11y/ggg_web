"""
handlers/upload.py — Handle .py, .zip uploads AND GitHub repo deploys.
"""

from __future__ import annotations

import asyncio
import io
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler,
    CommandHandler, filters, CallbackQueryHandler,
)

from config import MAX_UPLOAD_BYTES, MAX_ZIP_BYTES, BotState, ALLOWED_EXTENSIONS
from database import AsyncSessionLocal, Bot
from keyboards.main import cancel_kb, main_menu
from services.file_manager import (
    bot_directory, detect_entry_points, detect_requirements,
    extract_zip_bot, save_single_py,
)
from services.installer import install_requirements
from utils.security import is_admin, safe_slug

# Conversation states
WAIT_FILE, WAIT_NAME, WAIT_ENTRY, WAIT_GIT_URL = range(4)

_upload_ctx: dict[int, dict] = {}   # user_id → temp state


# ═══════════════════════════════════════════════════════════════════════════
# Entry: choose method
# ═══════════════════════════════════════════════════════════════════════════

async def ask_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦  Upload .py / .zip file", callback_data="addbot_upload")],
        [InlineKeyboardButton("🐙  Deploy from GitHub repo", callback_data="addbot_github")],
    ])
    await update.message.reply_text(
        "➕ <b>Add New Bot</b>\n\nকীভাবে bot add করতে চান?",
        parse_mode="HTML",
        reply_markup=kb,
    )
    return WAIT_FILE


async def cb_addbot_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "📦 <b>Upload Bot File</b>\n\n"
        "Send your Python bot file.\n\n"
        "Supported: <code>.py</code>  |  <code>.zip</code>",
        parse_mode="HTML",
    )
    return WAIT_FILE


async def cb_addbot_github(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🐙 <b>Deploy from GitHub</b>\n\n"
        "আপনার repo-র URL পাঠান।\n\n"
        "Examples:\n"
        "<code>https://github.com/user/repo</code>\n"
        "<code>https://github.com/user/repo.git</code>",
        parse_mode="HTML",
    )
    return WAIT_GIT_URL


# ═══════════════════════════════════════════════════════════════════════════
# File upload flow (.py / .zip)
# ═══════════════════════════════════════════════════════════════════════════

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
        await update.message.reply_text("❌ Only .py and .zip files are supported.")
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

    _upload_ctx[user_id] = {
        "source": "file",
        "tmp_path": tmp_path,
        "suffix": suffix,
        "filename": filename,
    }
    await msg.edit_text("✅ File received!\n\n📝 What should this bot be named?")
    return WAIT_NAME


# ═══════════════════════════════════════════════════════════════════════════
# GitHub deploy flow — uses HTTP archive download (no git binary)
# ═══════════════════════════════════════════════════════════════════════════

def _parse_github_url(url: str):
    """Return (owner, repo) or None."""
    try:
        p = urlparse(url.strip())
        if p.scheme != "https" or p.netloc not in ("github.com", "www.github.com"):
            return None
        parts = [x for x in p.path.strip("/").split("/") if x]
        if len(parts) < 2:
            return None
        owner, repo = parts[0], parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        return owner, repo
    except Exception:
        return None


async def _download_bytes(url: str, timeout: int = 180) -> bytes | None:
    """Download URL → bytes. Uses httpx, falls back to urllib."""
    try:
        import httpx
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=timeout,
            headers={"User-Agent": "ShuvoHosting/3.0"},
        ) as client:
            r = await client.get(url)
            if r.status_code == 200:
                return r.content
            return None
    except ImportError:
        import urllib.request
        loop = asyncio.get_event_loop()

        def _fetch():
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.read()
            except Exception:
                return None

        return await loop.run_in_executor(None, _fetch)


async def receive_git_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return ConversationHandler.END

    url = update.message.text.strip()
    parsed = _parse_github_url(url)
    if not parsed:
        await update.message.reply_text(
            "❌ Valid GitHub URL না। আবার পাঠান।\n"
            "Example: <code>https://github.com/user/repo</code>",
            parse_mode="HTML",
        )
        return WAIT_GIT_URL

    owner, repo = parsed
    msg = await update.message.reply_text(
        f"⏳ Downloading <code>{owner}/{repo}</code>…", parse_mode="HTML"
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="gitbot_"))
    repo_dir = tmp_dir / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    # Try branches: main, master. codeload.github.com serves ZIP archive.
    zip_bytes = None
    tried = []
    for branch in ("main", "master"):
        archive_url = (
            f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"
        )
        tried.append(f"{branch}")
        try:
            result = await _download_bytes(archive_url)
            if result:
                zip_bytes = result
                break
        except Exception as e:
            tried.append(f"{branch} err: {e}")

    if not zip_bytes:
        await msg.edit_text(
            f"❌ Repo download failed.\n"
            f"Tried branches: <code>{', '.join(tried)}</code>\n\n"
            f"Repo public কি না নিশ্চিত করুন।",
            parse_mode="HTML",
        )
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return ConversationHandler.END

    # Extract
    try:
        extract_root = tmp_dir / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(extract_root)
    except Exception as e:
        await msg.edit_text(
            f"❌ Extract failed: <code>{e}</code>", parse_mode="HTML"
        )
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return ConversationHandler.END

    # GitHub archive top-level folder = <repo>-<branch>/
    children = [p for p in extract_root.iterdir() if p.is_dir()]
    src_root = children[0] if len(children) == 1 else extract_root

    # Move all contents into repo_dir
    try:
        for item in src_root.iterdir():
            dest = repo_dir / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(item), str(dest))
    except Exception as e:
        await msg.edit_text(f"❌ Move failed: <code>{e}</code>", parse_mode="HTML")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return ConversationHandler.END

    # Collect python files
    py_files = []
    for p in repo_dir.rglob("*.py"):
        rel = p.relative_to(repo_dir)
        if any(
            part in ("__pycache__", "venv", ".venv", "env", ".git")
            or part.startswith(".")
            for part in rel.parts
        ):
            continue
        py_files.append(str(rel).replace("\\", "/"))
    py_files.sort()

    if not py_files:
        await msg.edit_text("❌ Repo-তে কোনো .py file পাওয়া যায়নি।")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return ConversationHandler.END

    _upload_ctx[user_id] = {
        "source": "git",
        "url": url,
        "owner": owner,
        "repo": repo,
        "tmp_dir": str(tmp_dir),
        "repo_dir": str(repo_dir),
        "py_files": py_files,
    }

    await msg.edit_text(
        f"✅ Downloaded <code>{owner}/{repo}</code>\n\n"
        f"📝 এই bot-এর নাম কী দিতে চান?",
        parse_mode="HTML",
    )
    return WAIT_NAME


# ═══════════════════════════════════════════════════════════════════════════
# Shared: receive name
# ═══════════════════════════════════════════════════════════════════════════

async def receive_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    name    = update.message.text.strip()

    if not name or len(name) > 100:
        await update.message.reply_text("⚠️ Invalid name. Try again.")
        return WAIT_NAME

    slug = safe_slug(name)

    async with AsyncSessionLocal() as s:
        from sqlalchemy import select
        existing = await s.execute(select(Bot).where(Bot.slug == slug))
        if existing.scalars().first():
            await update.message.reply_text(
                f"❌ A bot named '{name}' already exists. Choose a different name."
            )
            return WAIT_NAME

    data = _upload_ctx.setdefault(user_id, {})
    data["name"] = name
    data["slug"] = slug

    if data.get("source") == "git":
        return await _process_git_clone(update, ctx, user_id, data)

    return await _process_file_upload(update, ctx, user_id, data)


# ── Git: move downloaded repo into bot_directory ─────────────────────────────

async def _process_git_clone(update, ctx, user_id, data) -> int:
    slug     = data["slug"]
    repo_dir = Path(data["repo_dir"])
    tmp_dir  = Path(data["tmp_dir"])
    py_files = data["py_files"]

    msg = await update.message.reply_text("⏳ Processing files...")

    target = bot_directory(slug)
    target.mkdir(parents=True, exist_ok=True)

    try:
        for item in repo_dir.iterdir():
            dest = target / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(item), str(dest))
    except Exception as e:
        await msg.edit_text(f"❌ Move failed: <code>{e}</code>", parse_mode="HTML")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _cleanup(user_id)
        return ConversationHandler.END

    shutil.rmtree(tmp_dir, ignore_errors=True)
    data["bot_dir"] = str(target)

    # Prefer common entry names
    preferred = ("bot.py", "main.py", "app.py", "run.py", "start.py")
    root_py = [p for p in py_files if "/" not in p]
    chosen = next((p for p in preferred if p in root_py), None)

    if chosen:
        data["entry"] = chosen
        await msg.edit_text(
            f"✅ Repo deployed. Entry: <code>{chosen}</code>",
            parse_mode="HTML",
        )
        return await _finish_upload(update, ctx, user_id)

    if len(py_files) == 1:
        data["entry"] = py_files[0]
        await msg.edit_text(
            f"✅ Entry detected: <code>{py_files[0]}</code>",
            parse_mode="HTML",
        )
        return await _finish_upload(update, ctx, user_id)

    top = py_files[:20]
    buttons = [
        [InlineKeyboardButton(f, callback_data=f"entry_select:{user_id}:{f}")]
        for f in top
    ]
    await msg.edit_text(
        "📂 Multiple Python files found.\nSelect the <b>main entry point</b>:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return WAIT_ENTRY


# ── File: original .py/.zip processing ───────────────────────────────────────

async def _process_file_upload(update, ctx, user_id, data) -> int:
    suffix   = data["suffix"]
    tmp_path = Path(data["tmp_path"])
    slug     = data["slug"]

    msg = await update.message.reply_text("⏳ Processing files...")

    if suffix == ".py":
        content = tmp_path.read_bytes()
        await save_single_py(data["filename"], content, slug)
        data["entry"] = Path(data["filename"]).name
        tmp_path.unlink(missing_ok=True)
        await msg.edit_text(
            f"✅ File saved as <code>{Path(data['filename']).name}</code>",
            parse_mode="HTML",
        )
        return await _finish_upload(update, ctx, user_id)

    # .zip
    bot_dir, py_files = await extract_zip_bot(tmp_path, slug)
    tmp_path.unlink(missing_ok=True)
    data["bot_dir"] = str(bot_dir)

    if not py_files:
        await msg.edit_text("❌ No .py files found in ZIP.")
        _cleanup(user_id)
        return ConversationHandler.END

    if len(py_files) == 1:
        data["entry"] = py_files[0]
        await msg.edit_text(
            f"✅ ZIP extracted. Auto-detected entry: <code>{py_files[0]}</code>",
            parse_mode="HTML",
        )
        return await _finish_upload(update, ctx, user_id)

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


# ═══════════════════════════════════════════════════════════════════════════
# Entry selection callback
# ═══════════════════════════════════════════════════════════════════════════

async def receive_entry_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query    = update.callback_query
    await query.answer()
    _, uid, entry = query.data.split(":", 2)
    user_id  = int(uid)

    if user_id != update.effective_user.id:
        return WAIT_ENTRY

    _upload_ctx[user_id]["entry"] = entry
    await query.edit_message_text(
        f"✅ Entry point selected: <code>{entry}</code>", parse_mode="HTML"
    )
    return await _finish_upload(update, ctx, user_id)


# ═══════════════════════════════════════════════════════════════════════════
# Finish: DB persist + install requirements
# ═══════════════════════════════════════════════════════════════════════════

async def _finish_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE, user_id: int) -> int:
    data     = _upload_ctx.get(user_id, {})
    name     = data.get("name", "Bot")
    slug     = data.get("slug", safe_slug(name))
    entry    = data.get("entry")
    bot_dir  = data.get("bot_dir") or str(bot_directory(slug))
    source   = data.get("source", "file")
    url      = data.get("url", "")

    msg_obj = update.message or (
        update.callback_query.message if update.callback_query else None
    )

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

    from services.audit import log_action
    src_label = f"GitHub: {url}" if source == "git" else "Upload"
    await log_action(f"Added bot: {name} ({src_label})", bot_name=name)

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

    await msg_obj.reply_text(
        f"✅ <b>Bot added successfully!</b>\n\n"
        f"🤖 Name: <b>{name}</b>\n"
        f"📄 Entry: <code>{entry}</code>\n"
        f"📥 Source: <code>{src_label}</code>\n\n"
        f"Use '🤖 My Bots' to start it.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )

    _cleanup(user_id)
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
# Cancel + cleanup
# ═══════════════════════════════════════════════════════════════════════════

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
    tmp_dir = data.get("tmp_dir")
    if tmp_dir:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
# Conversation handler
# ═══════════════════════════════════════════════════════════════════════════

def upload_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^➕ Add Bot$"), ask_upload),
            CallbackQueryHandler(cb_addbot_upload, pattern="^addbot_upload$"),
            CallbackQueryHandler(cb_addbot_github, pattern="^addbot_github$"),
        ],
        states={
            WAIT_FILE: [
                MessageHandler(filters.Document.ALL, receive_file),
                CallbackQueryHandler(cb_addbot_upload, pattern="^addbot_upload$"),
                CallbackQueryHandler(cb_addbot_github, pattern="^addbot_github$"),
            ],
            WAIT_GIT_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_git_url),
            ],
            WAIT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name),
            ],
            WAIT_ENTRY: [
                CallbackQueryHandler(receive_entry_callback, pattern=r"^entry_select:"),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), cancel_upload),
            CommandHandler("cancel", cancel_upload),
        ],
        per_message=False,
    )
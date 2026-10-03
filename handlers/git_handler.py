"""
handlers/git_handler.py — Git deploy: link repo, clone, pull, auto-requirements.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)

from database import AsyncSessionLocal, Bot, GitDeploy
from keyboards.main import cancel_kb, main_menu
from services.audit import log_action
from services.git_deploy import git_clone, git_pull, write_auto_requirements
from services.installer import install_requirements
from utils.security import is_admin
from sqlalchemy import select

WAIT_REPO_URL, WAIT_BRANCH = range(2)
_git_ctx: dict[int, dict] = {}


def git_kb(bot_id: int, has_git: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_git:
        rows.append([
            InlineKeyboardButton("🔄 Pull Latest",       callback_data=f"git_pull:{bot_id}"),
            InlineKeyboardButton("🔗 Change Repo",       callback_data=f"git_link:{bot_id}"),
        ])
        rows.append([
            InlineKeyboardButton("📦 Auto-Requirements", callback_data=f"git_autoreq:{bot_id}"),
        ])
    else:
        rows.append([InlineKeyboardButton("🔗 Link Git Repo", callback_data=f"git_link:{bot_id}")])

    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"bot_detail:{bot_id}")])
    return InlineKeyboardMarkup(rows)


async def cb_bot_git(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot    = await s.get(Bot, bot_id)
        result = await s.execute(select(GitDeploy).where(GitDeploy.bot_id == bot_id))
        gd     = result.scalars().first()

    if not bot:
        await query.edit_message_text("❌ Bot not found.")
        return

    if gd:
        text = (
            f"🔗 <b>Git Deploy — {bot.name}</b>\n\n"
            f"Repo:   <code>{gd.repo_url}</code>\n"
            f"Branch: <code>{gd.branch}</code>\n"
            f"Commit: <code>{gd.last_commit or '—'}</code>\n"
            f"Last deploy: {gd.last_deploy.strftime('%Y-%m-%d %H:%M') if gd.last_deploy else '—'}"
        )
    else:
        text = f"🔗 <b>Git Deploy — {bot.name}</b>\n\nNo repository linked yet."

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=git_kb(bot_id, bool(gd)))


async def cb_git_link_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query   = update.callback_query
    await query.answer()
    bot_id  = int(query.data.split(":")[1])
    user_id = update.effective_user.id

    _git_ctx[user_id] = {"bot_id": bot_id}
    await query.message.reply_text(
        "🔗 Enter the <b>Git repository URL</b>:\n\n"
        "Example:\n<code>https://github.com/user/my-bot.git</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    return WAIT_REPO_URL


async def git_receive_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    url     = update.message.text.strip()

    if not url.startswith(("https://", "http://", "git@")):
        await update.message.reply_text("❌ Invalid URL. Must start with https:// or git@")
        return WAIT_REPO_URL

    _git_ctx[user_id]["repo_url"] = url
    await update.message.reply_text(
        "🌿 Enter the <b>branch name</b> (or send <code>main</code>):",
        parse_mode="HTML",
    )
    return WAIT_BRANCH


async def git_receive_branch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    branch  = update.message.text.strip() or "main"
    data    = _git_ctx.pop(user_id, {})
    bot_id  = data.get("bot_id")
    repo_url = data.get("repo_url", "")

    msg = await update.message.reply_text(
        f"⏳ Cloning <code>{repo_url}</code> ({branch})…",
        parse_mode="HTML",
    )

    ok, result = await git_clone(bot_id, repo_url, branch)
    await msg.edit_text(result, parse_mode="HTML")

    if ok:
        # Try auto-install requirements
        notice = await update.message.reply_text("⏳ Installing dependencies…")
        ok2, msg2 = await install_requirements(bot_id)
        await notice.edit_text(msg2, parse_mode="HTML")

        await log_action(f"Git clone: {repo_url}", bot_name=str(bot_id))

    await update.message.reply_text("Done.", reply_markup=main_menu())
    return ConversationHandler.END


async def cb_git_pull(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Pulling…")
    bot_id = int(query.data.split(":")[1])

    await query.edit_message_text("⏳ Pulling latest changes…")
    ok, result = await git_pull(bot_id)
    await query.edit_message_text(result, parse_mode="HTML")

    if ok:
        from services.process_manager import restart_bot
        ok2, msg2 = await restart_bot(bot_id)
        await query.message.reply_text(f"🔄 {msg2}")
        await log_action("Git pull + restart", bot_name=str(bot_id))


async def cb_git_autoreq(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Scanning imports…")
    bot_id = int(query.data.split(":")[1])

    await query.edit_message_text("🔍 Scanning Python imports…")
    ok, result = await write_auto_requirements(bot_id)
    await query.edit_message_text(result, parse_mode="HTML")

    if ok:
        notice = await query.message.reply_text("⏳ Installing detected packages…")
        ok2, msg2 = await install_requirements(bot_id)
        await notice.edit_text(msg2, parse_mode="HTML")


async def git_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    _git_ctx.pop(update.effective_user.id, None)
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu())
    return ConversationHandler.END


def git_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_git_link_start, pattern=r"^git_link:")],
        states={
            WAIT_REPO_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, git_receive_url)],
            WAIT_BRANCH:   [MessageHandler(filters.TEXT & ~filters.COMMAND, git_receive_branch)],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), git_cancel),
            CommandHandler("cancel", git_cancel),
        ],
        per_message=False,
    )

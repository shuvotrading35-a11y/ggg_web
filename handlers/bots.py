"""
handlers/bots.py — Bot list, detail view, control actions via callbacks.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import STATE_EMOJI, BotState
from database import AsyncSessionLocal, Bot, BotVersion
from keyboards.bots import bot_detail_kb, bot_list_kb, versions_kb
from keyboards.main import main_menu
from services.audit import log_action
from services.file_manager import backup_bot, delete_bot_files, export_bot_zip, rollback_bot
from services.process_manager import restart_bot, start_bot, stop_bot, get_process_info
from services.resource_monitor import bot_disk_usage
from utils.formatters import fmt_bytes, fmt_ram, fmt_uptime
from utils.security import is_admin
from sqlalchemy import select


# ── Bot list ──────────────────────────────────────────────────────────────────

async def show_bot_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return

    async with AsyncSessionLocal() as s:
        result = await s.execute(select(Bot).order_by(Bot.name))
        bots   = result.scalars().all()

    src = update.message or update.callback_query.message
    if not bots:
        await src.reply_text("🤖 No bots hosted yet. Use ➕ Add Bot to get started.", reply_markup=main_menu())
        return

    text = "🤖 <b>My Bots</b>\n\n"
    for i, b in enumerate(bots, 1):
        emoji = STATE_EMOJI.get(b.state, "❓")
        text += f"{i}. {emoji} <b>{b.name}</b>\n"

    await src.reply_text(text, parse_mode="HTML", reply_markup=bot_list_kb(bots))


# ── Bot detail ────────────────────────────────────────────────────────────────

async def cb_bot_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        await query.edit_message_text("❌ Bot not found.")
        return

    info    = await get_process_info(bot)
    uptime  = fmt_uptime(bot.started_at)
    emoji   = STATE_EMOJI.get(bot.state, "❓")
    cpu     = f"{info['cpu']:.1f}%" if info["cpu"] else "—"
    ram     = fmt_ram(info["ram_mb"]) if info["ram_mb"] else "—"

    text = (
        f"🤖 <b>{bot.name}</b>\n\n"
        f"Status: {emoji} {bot.state.capitalize()}\n"
        f"Entry:  <code>{bot.entry_file or '—'}</code>\n"
        f"PID:    <code>{bot.pid or '—'}</code>\n"
        f"CPU:    {cpu}\n"
        f"RAM:    {ram}\n"
        f"Uptime: {uptime}\n"
        f"Restarts: {bot.restart_count}\n"
        f"Crash loop: {'⚠️ Yes' if bot.crash_loop else '✅ No'}\n"
        f"Auto-start: {'✅' if bot.auto_start else '❌'}\n"
    )
    if bot.last_error:
        text += f"\n⚠️ Last error:\n<pre>{bot.last_error[:200]}</pre>"

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=bot_detail_kb(bot))


# ── Start / Stop / Restart ────────────────────────────────────────────────────

async def cb_bot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Starting…")
    bot_id = int(query.data.split(":")[1])

    ok, msg = await start_bot(bot_id)
    await query.edit_message_text(msg, parse_mode="HTML")

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if bot:
        await log_action(f"Started bot: {bot.name}", bot_name=bot.name)
        from services.notifier import notify_started
        await notify_started(bot.name)
    await cb_bot_detail(update, ctx)


async def cb_bot_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Stopping…")
    bot_id = int(query.data.split(":")[1])

    ok, msg = await stop_bot(bot_id)
    await query.edit_message_text(msg)

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if bot:
        await log_action(f"Stopped bot: {bot.name}", bot_name=bot.name)
        from services.notifier import notify_stopped
        await notify_stopped(bot.name)
    await cb_bot_detail(update, ctx)


async def cb_bot_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Restarting…")
    bot_id = int(query.data.split(":")[1])

    ok, msg = await restart_bot(bot_id)
    await query.edit_message_text(msg)

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if bot:
        await log_action(f"Restarted bot: {bot.name}", bot_name=bot.name)
    await cb_bot_detail(update, ctx)


# ── Resources ─────────────────────────────────────────────────────────────────

async def cb_bot_resources(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        await query.edit_message_text("❌ Bot not found.")
        return

    info  = await get_process_info(bot)
    disk  = bot_disk_usage(bot.directory)
    uptime = fmt_uptime(bot.started_at)

    text = (
        f"📊 <b>Resources — {bot.name}</b>\n\n"
        f"CPU:     {info['cpu']:.1f}%\n"
        f"RAM:     {fmt_ram(info['ram_mb'])}\n"
        f"PID:     {bot.pid or '—'}\n"
        f"Storage: {fmt_bytes(disk)}\n"
        f"Uptime:  {uptime}\n"
    )
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data=f"bot_res:{bot_id}"),
        InlineKeyboardButton("⬅️ Back",    callback_data=f"bot_detail:{bot_id}"),
    ]])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)


# ── Backup & Versions ─────────────────────────────────────────────────────────

async def cb_bot_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Backing up…")
    bot_id = int(query.data.split(":")[1])

    path = await backup_bot(bot_id)
    if path:
        await query.edit_message_text(f"✅ Backup created: <code>{path.name}</code>", parse_mode="HTML")
        await log_action("Backup", bot_name=str(bot_id))
    else:
        await query.edit_message_text("❌ Backup failed.")


async def cb_bot_versions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(BotVersion)
            .where(BotVersion.bot_id == bot_id)
            .order_by(BotVersion.version_num.desc())
        )
        versions = result.scalars().all()

    if not versions:
        await query.edit_message_text("No backups yet. Use 📦 Backup first.")
        return

    await query.edit_message_text(
        "🔄 <b>Select a version to rollback:</b>",
        parse_mode="HTML",
        reply_markup=versions_kb(bot_id, versions),
    )


async def cb_bot_rollback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query    = update.callback_query
    await query.answer("Rolling back…")
    _, bot_id_str, ver_id_str = query.data.split(":")
    bot_id, ver_id = int(bot_id_str), int(ver_id_str)

    ok, msg = await rollback_bot(bot_id, ver_id)
    await query.edit_message_text(msg)
    await log_action(f"Rollback to version {ver_id}", bot_name=str(bot_id))


# ── Export ────────────────────────────────────────────────────────────────────

async def cb_bot_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Preparing export…")
    bot_id = int(query.data.split(":")[1])

    path = await export_bot_zip(bot_id)
    if not path:
        await query.edit_message_text("❌ Export failed.")
        return

    await query.message.reply_document(document=open(path, "rb"), filename=path.name)
    path.unlink(missing_ok=True)


# ── Delete ────────────────────────────────────────────────────────────────────

async def cb_bot_delete_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        await query.edit_message_text("❌ Bot not found.")
        return

    from keyboards.main import confirm_kb
    await query.edit_message_text(
        f"⚠️ <b>Delete {bot.name}?</b>\n\n"
        "This will permanently delete:\n"
        "• Bot files\n• Virtual environment\n• Logs\n• Configuration\n• Database record",
        parse_mode="HTML",
        reply_markup=confirm_kb("bot_delete", bot_id),
    )


async def cb_bot_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Deleting…")
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if not bot:
            await query.edit_message_text("❌ Bot not found.")
            return
        name = bot.name

    # Stop first
    await stop_bot(bot_id)

    # Delete files
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        await delete_bot_files(bot)
        await s.delete(bot)
        await s.commit()

    await log_action(f"Deleted bot: {name}", bot_name=name)
    await query.edit_message_text(f"🗑 <b>{name}</b> has been deleted.", parse_mode="HTML")


# ── Ping ──────────────────────────────────────────────────────────────────────

async def cb_bot_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Pinging…")
    bot_id = int(query.data.split(":")[1])

    from services.health_checker import run_health_check
    ok, detail = await run_health_check(bot_id)

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    name = bot.name if bot else str(bot_id)

    status = "✅ Healthy" if ok else "❌ Unhealthy"
    await query.edit_message_text(
        f"🏓 <b>Health Check — {name}</b>\n\n{status}\n{detail}",
        parse_mode="HTML",
    )


# ── Callback back to list ─────────────────────────────────────────────────────

async def cb_bot_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await show_bot_list(update, ctx)

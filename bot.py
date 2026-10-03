"""
bot.py — Shuvo Hosting Manager entry point.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from telegram import Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    MessageHandler, filters
)

from config import BOT_TOKEN, LOG_LEVEL
from database import init_db

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/opt/shuvo-hosting/data/manager.log", mode="a"),
    ],
)
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    """Called after the bot is initialised — start background tasks."""
    from services.notifier import set_bot, notify_reboot_restore
    from services.supervisor import supervisor_loop, set_notify_callback
    from services.resource_monitor import perf_sampler_loop
    from services.health_checker import health_check_loop
    from services.cron_scheduler import load_schedules, start_scheduler
    from services.process_manager import restore_auto_start_bots

    set_bot(application.bot)

    async def _notify_cb(msg: str, bot_name: str, bot_id: int):
        from services.notifier import notify
        await notify(msg, critical=True)

    set_notify_callback(_notify_cb)

    # Restore auto-start bots
    logger.info("Restoring auto-start bots after reboot…")
    restored = await restore_auto_start_bots()
    if restored:
        await notify_reboot_restore(restored)
        logger.info(f"Restored: {restored}")

    # Start background tasks
    asyncio.create_task(supervisor_loop())
    asyncio.create_task(perf_sampler_loop())
    asyncio.create_task(health_check_loop())

    # APScheduler
    await load_schedules()
    start_scheduler()

    logger.info("Shuvo Hosting Manager is running ✅")


def build_app() -> Application:
    # Import handlers
    from handlers.start   import cmd_start, cmd_help, cmd_status
    from handlers.upload  import upload_conversation
    from handlers.bots    import (
        show_bot_list, cb_bot_detail, cb_bot_start, cb_bot_stop,
        cb_bot_restart, cb_bot_resources, cb_bot_backup, cb_bot_versions,
        cb_bot_rollback, cb_bot_export, cb_bot_delete_confirm, cb_bot_delete,
        cb_bot_ping, cb_bot_list,
    )
    from handlers.logs    import (
        cb_bot_logs, cb_bot_logs_download, cb_bot_logs_clear, show_system_logs
    )
    from handlers.env_vars import (
        cb_bot_env, cb_env_view, env_add_conversation, env_del_conversation
    )
    from handlers.stats   import (
        show_server_stats, show_bulk_actions, toggle_maintenance,
        cb_bulk_start, cb_bulk_stop, cb_bulk_restart,
        cb_bulk_backup, cb_bulk_clear_logs,
    )
    from handlers.settings import (
        show_settings, cb_notif_toggle,
        cmd_restart_bot, cmd_stop_bot, cmd_start_bot,
        cmd_logs_quick, cmd_backup_all,
    )
    from handlers.vault   import (
        show_vault, vault_add_conversation, vault_del_conversation
    )
    from handlers.file_manager import (
        cb_open_file_manager, cb_fm_cd, cb_fm_file,
        cb_fm_download, cb_fm_del_confirm, cb_fm_delete,
        cb_fm_zipdir, cb_fm_search,
        fm_upload_conversation, fm_mkdir_conversation, fm_rename_conversation,
    )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # ── Conversations (must come before generic handlers) ─────────────────────
    app.add_handler(upload_conversation())
    app.add_handler(env_add_conversation())
    app.add_handler(env_del_conversation())
    app.add_handler(vault_add_conversation())
    app.add_handler(vault_del_conversation())
    app.add_handler(fm_upload_conversation())
    app.add_handler(fm_mkdir_conversation())
    app.add_handler(fm_rename_conversation())

    # ── Commands ──────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("bots",        show_bot_list))
    app.add_handler(CommandHandler("stats",       show_server_stats))
    app.add_handler(CommandHandler("status",      cmd_status))
    app.add_handler(CommandHandler("restart",     cmd_restart_bot))
    app.add_handler(CommandHandler("stop",        cmd_stop_bot))
    app.add_handler(CommandHandler("start_bot",   cmd_start_bot))
    app.add_handler(CommandHandler("logs",        cmd_logs_quick))
    app.add_handler(CommandHandler("backup",      cmd_backup_all))
    app.add_handler(CommandHandler("maintenance", toggle_maintenance))

    # ── Reply keyboard ────────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Regex("^🤖 My Bots$"),    show_bot_list))
    app.add_handler(MessageHandler(filters.Regex("^📊 Server Stats$"), show_server_stats))
    app.add_handler(MessageHandler(filters.Regex("^⚙️ Settings$"),    show_settings))
    app.add_handler(MessageHandler(filters.Regex("^📜 System Logs$"), show_system_logs))
    app.add_handler(MessageHandler(filters.Regex("^🔐 Token Vault$"), show_vault))
    app.add_handler(MessageHandler(filters.Regex("^⚡ Bulk Actions$"), show_bulk_actions))
    app.add_handler(MessageHandler(filters.Regex("^⏰ Schedules$"),    _placeholder("Schedules coming soon.")))
    app.add_handler(MessageHandler(filters.Regex("^🔄 Refresh$"),     cmd_start))
    app.add_handler(MessageHandler(filters.Regex("^ℹ️ Help$"),        cmd_help))

    # ── Inline callbacks ──────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(cb_bot_list,           pattern="^bot_list$"))
    app.add_handler(CallbackQueryHandler(cb_bot_detail,         pattern=r"^bot_detail:"))
    app.add_handler(CallbackQueryHandler(cb_bot_start,          pattern=r"^bot_start:"))
    app.add_handler(CallbackQueryHandler(cb_bot_stop,           pattern=r"^bot_stop:"))
    app.add_handler(CallbackQueryHandler(cb_bot_restart,        pattern=r"^bot_restart:"))
    app.add_handler(CallbackQueryHandler(cb_bot_resources,      pattern=r"^bot_res:"))
    app.add_handler(CallbackQueryHandler(cb_bot_logs,           pattern=r"^bot_logs:"))
    app.add_handler(CallbackQueryHandler(cb_bot_logs_download,  pattern=r"^bot_logs_dl:"))
    app.add_handler(CallbackQueryHandler(cb_bot_logs_clear,     pattern=r"^bot_logs_clear:"))
    app.add_handler(CallbackQueryHandler(cb_bot_env,            pattern=r"^bot_env:"))
    app.add_handler(CallbackQueryHandler(cb_env_view,           pattern=r"^env_view:"))
    app.add_handler(CallbackQueryHandler(cb_bot_backup,         pattern=r"^bot_backup:"))
    app.add_handler(CallbackQueryHandler(cb_bot_versions,       pattern=r"^bot_versions:"))
    app.add_handler(CallbackQueryHandler(cb_bot_rollback,       pattern=r"^bot_rollback:"))
    app.add_handler(CallbackQueryHandler(cb_bot_export,         pattern=r"^bot_export:"))
    app.add_handler(CallbackQueryHandler(cb_bot_delete_confirm, pattern=r"^bot_delete_confirm:"))
    app.add_handler(CallbackQueryHandler(cb_bot_delete,         pattern=r"^bot_delete:"))
    app.add_handler(CallbackQueryHandler(cb_bot_ping,           pattern=r"^bot_ping:"))
    app.add_handler(CallbackQueryHandler(cb_bulk_start,         pattern="^bulk_start$"))
    app.add_handler(CallbackQueryHandler(cb_bulk_stop,          pattern="^bulk_stop$"))
    app.add_handler(CallbackQueryHandler(cb_bulk_restart,       pattern="^bulk_restart$"))
    app.add_handler(CallbackQueryHandler(cb_bulk_backup,        pattern="^bulk_backup$"))
    app.add_handler(CallbackQueryHandler(cb_bulk_clear_logs,    pattern="^bulk_clear_logs$"))
    app.add_handler(CallbackQueryHandler(cb_notif_toggle,       pattern=r"^notif_toggle:"))
    app.add_handler(CallbackQueryHandler(_cb_cancel,            pattern="^cancel$"))

    # ── File Manager callbacks ────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(cb_open_file_manager, pattern=r"^fm_open:"))
    app.add_handler(CallbackQueryHandler(cb_fm_cd,             pattern=r"^fm_cd:"))
    app.add_handler(CallbackQueryHandler(cb_fm_file,           pattern=r"^fm_file:"))
    app.add_handler(CallbackQueryHandler(cb_fm_download,       pattern=r"^fm_download:"))
    app.add_handler(CallbackQueryHandler(cb_fm_del_confirm,    pattern=r"^fm_del_confirm:"))
    app.add_handler(CallbackQueryHandler(cb_fm_delete,         pattern=r"^fm_delete:"))
    app.add_handler(CallbackQueryHandler(cb_fm_zipdir,         pattern=r"^fm_zipdir:"))
    app.add_handler(CallbackQueryHandler(cb_fm_search,         pattern=r"^fm_search:"))

    return app


def _placeholder(text: str):
    async def _h(update: Update, ctx) -> None:
        await update.message.reply_text(text)
    return MessageHandler(filters.TEXT, _h)  # won't actually be used for regex handlers


async def _cb_cancel(update: Update, ctx) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Cancelled.")


def main() -> None:
    import os
    os.makedirs("/opt/shuvo-hosting/data", exist_ok=True)

    async def _run():
        await init_db()
        app = build_app()
        async with app:
            await app.start()
            await app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
            logger.info("Polling started.")
            await asyncio.Event().wait()   # run forever

    asyncio.run(_run())


if __name__ == "__main__":
    main()

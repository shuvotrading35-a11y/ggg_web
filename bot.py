"""
bot.py — Shuvo Hosting Manager v3.0 — Full-featured VPS Bot Manager.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    MessageHandler, filters
)

from config import BOT_TOKEN, LOG_LEVEL, DATA_DIR
from database import init_db

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR = Path(DATA_DIR) if DATA_DIR else Path("/app/data")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "manager.log"

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Graceful shutdown (FIXED)
# ═══════════════════════════════════════════════════════════════════════════

_shutting_down = False
_shutdown_event = None


def _sync_stop_all_bots() -> None:
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True

    logger.info("Cleaning up hosted bots…")
    try:
        from services.process_manager import kill_all_hosted_bots
        # নতুন লুপ তৈরি করা এখন নিরাপদ, কারণ মেইন লুপ অলরেডি বন্ধ হয়ে গেছে
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                asyncio.wait_for(kill_all_hosted_bots(), timeout=15)
            )
        except Exception as e:
            logger.warning(f"kill_all_hosted_bots error: {e}")
        finally:
            try:
                loop.close()
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"cleanup import failed: {e}")


def _setup_signal_handlers() -> None:
    def _handler(signum, frame):
        global _shutdown_event
        logger.info(f"Signal {signum} received — stopping manager…")
        if _shutdown_event and not _shutdown_event.is_set():
            _shutdown_event.set()
        else:
            # Fallback
            _sync_stop_all_bots()
            sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)
    except Exception as e:
        logger.warning(f"signal setup failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# Startup
# ═══════════════════════════════════════════════════════════════════════════

async def post_init(application: Application) -> None:
    from services.notifier        import set_bot, notify_reboot_restore
    from services.supervisor      import supervisor_loop, set_notify_callback
    from services.resource_monitor import perf_sampler_loop
    from services.health_checker   import health_check_loop
    from services.cron_scheduler   import load_schedules, start_scheduler, get_scheduler
    from services.process_manager  import restore_auto_start_bots
    from services.uptime_tracker   import uptime_loop
    from services.restart_rules    import rules_loop
    from services.daily_report     import schedule_daily_report
    from services.watchdog_service import init_watchdog
    from services.auto_updater     import auto_update_loop

    set_bot(application.bot)

    async def _notify_cb(msg: str, bot_name: str, bot_id: int):
        from services.notifier import notify
        await notify(msg, critical=True)

    set_notify_callback(_notify_cb)

    logger.info("Restoring auto-start bots after reboot…")
    restored = await restore_auto_start_bots()
    if restored:
        await notify_reboot_restore(restored)
        logger.info(f"Restored: {restored}")

    asyncio.create_task(supervisor_loop())
    asyncio.create_task(perf_sampler_loop())
    asyncio.create_task(health_check_loop())
    asyncio.create_task(uptime_loop())
    asyncio.create_task(rules_loop())
    asyncio.create_task(auto_update_loop())

    await load_schedules()
    schedule_daily_report(get_scheduler())
    start_scheduler()

    loop = asyncio.get_event_loop()
    await init_watchdog(loop)

    logger.info("Shuvo Hosting Manager v3.0 is running ✅")


def build_app() -> Application:
    # ── Import all handlers ───────────────────────────────────────────────────
    from handlers.start      import cmd_start, cmd_help, cmd_status
    from handlers.upload     import upload_conversation
    from handlers.bots       import (
        show_bot_list, cb_bot_detail, cb_bot_start, cb_bot_stop,
        cb_bot_restart, cb_bot_resources, cb_bot_backup, cb_bot_versions,
        cb_bot_rollback, cb_bot_export, cb_bot_delete_confirm, cb_bot_delete,
        cb_bot_list,
    )
    from handlers.logs       import (
        cb_bot_logs, cb_bot_logs_download, cb_bot_logs_clear, show_system_logs
    )
    from handlers.env_vars   import (
        cb_bot_env, cb_env_view,
        env_add_conversation, env_del_conversation, env_raw_conversation,
    )
    from handlers.stats      import (
        show_server_stats, show_bulk_actions, toggle_maintenance,
        cb_bulk_start, cb_bulk_stop, cb_bulk_restart,
        cb_bulk_backup, cb_bulk_clear_logs,
    )
    from handlers.settings   import (
        show_settings, cb_notif_toggle,
        cmd_restart_bot, cmd_stop_bot, cmd_start_bot,
        cmd_logs_quick, cmd_backup_all,
    )
    from handlers.vault      import (
        show_vault, vault_add_conversation, vault_del_conversation
    )
    from handlers.file_manager import (
        cb_open_file_manager, cb_fm_cd, cb_fm_file,
        cb_fm_download, cb_fm_del_confirm, cb_fm_delete,
        cb_fm_zipdir, cb_fm_search,
        fm_upload_conversation, fm_mkdir_conversation, fm_rename_conversation,
    )
    from handlers.terminal   import show_terminal, terminal_conversation
    from handlers.git_handler import (
        cb_bot_git, cb_git_pull, cb_git_autoreq, git_conversation,
        cb_git_check, cb_git_toggle_auto,
    )
    from handlers.advanced   import (
        cb_bot_uptime, cb_bot_notes, cb_note_edit_start,
        cb_bot_clone_start, cb_bot_rules, cb_rule_add_start, cb_rule_clear,
        cb_bot_integrity, cb_integrity_record,
        show_port_manager,
        note_conversation, clone_conversation, rule_conversation,
    )
    from handlers.extras import (
        cb_bot_ping, cb_bot_deps, cb_deps_update,
        cb_bot_health, cb_bot_schedule, cb_schedule_clear,
        cmd_schedule_add,
    )
    from services.daily_report import send_daily_report

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # ── Conversations ─────────────────────────────────────────────────────────
    app.add_handler(upload_conversation())
    app.add_handler(env_add_conversation())
    app.add_handler(env_del_conversation())
    app.add_handler(env_raw_conversation())
    app.add_handler(vault_add_conversation())
    app.add_handler(vault_del_conversation())
    app.add_handler(fm_upload_conversation())
    app.add_handler(fm_mkdir_conversation())
    app.add_handler(fm_rename_conversation())
    app.add_handler(terminal_conversation())
    app.add_handler(git_conversation())
    app.add_handler(note_conversation())
    app.add_handler(clone_conversation())
    app.add_handler(rule_conversation())

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
    app.add_handler(CommandHandler("ports",       show_port_manager))
    app.add_handler(CommandHandler("report",      lambda u, c: send_daily_report()))
    app.add_handler(CommandHandler("schedule_add", cmd_schedule_add))

    # ── Reply keyboard ────────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Regex("^🤖 My Bots$"),      show_bot_list))
    app.add_handler(MessageHandler(filters.Regex("^📊 Server Stats$"),  show_server_stats))
    app.add_handler(MessageHandler(filters.Regex("^⚙️ Settings$"),      show_settings))
    app.add_handler(MessageHandler(filters.Regex("^📜 System Logs$"),   show_system_logs))
    app.add_handler(MessageHandler(filters.Regex("^🔐 Token Vault$"),   show_vault))
    app.add_handler(MessageHandler(filters.Regex("^⚡ Bulk Actions$"),  show_bulk_actions))
    app.add_handler(MessageHandler(filters.Regex("^💻 Terminal$"),      show_terminal))
    app.add_handler(MessageHandler(filters.Regex("^🔌 Port Manager$"),  show_port_manager))
    app.add_handler(MessageHandler(filters.Regex("^📊 Daily Report$"),  _send_report))
    app.add_handler(MessageHandler(filters.Regex("^⏰ Schedules$"),     _placeholder("⏰ Schedules — use /start_bot, /stop, /restart for quick actions.")))
    app.add_handler(MessageHandler(filters.Regex("^🔄 Refresh$"),       cmd_start))
    app.add_handler(MessageHandler(filters.Regex("^ℹ️ Help$"),          cmd_help))

    # ── Bot management callbacks ──────────────────────────────────────────────
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

    # ── Extras: ping, deps, health, schedule ─────────────────────────────────
    app.add_handler(CallbackQueryHandler(cb_bot_ping,           pattern=r"^bot_ping:"))
    app.add_handler(CallbackQueryHandler(cb_bot_deps,           pattern=r"^bot_deps:"))
    app.add_handler(CallbackQueryHandler(cb_deps_update,        pattern=r"^deps_update:"))
    app.add_handler(CallbackQueryHandler(cb_bot_health,         pattern=r"^bot_health:"))
    app.add_handler(CallbackQueryHandler(cb_bot_schedule,       pattern=r"^bot_schedule:"))
    app.add_handler(CallbackQueryHandler(cb_schedule_clear,     pattern=r"^schedule_clear:"))

    # ── Bulk callbacks ────────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(cb_bulk_start,         pattern="^bulk_start$"))
    app.add_handler(CallbackQueryHandler(cb_bulk_stop,          pattern="^bulk_stop$"))
    app.add_handler(CallbackQueryHandler(cb_bulk_restart,       pattern="^bulk_restart$"))
    app.add_handler(CallbackQueryHandler(cb_bulk_backup,        pattern="^bulk_backup$"))
    app.add_handler(CallbackQueryHandler(cb_bulk_clear_logs,    pattern="^bulk_clear_logs$"))

    # ── Settings callbacks ────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(cb_notif_toggle,       pattern=r"^notif_toggle:"))
    app.add_handler(CallbackQueryHandler(_cb_cancel,            pattern="^cancel$"))

    # ── File Manager callbacks ────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(cb_open_file_manager,  pattern=r"^fm_open:"))
    app.add_handler(CallbackQueryHandler(cb_fm_cd,              pattern=r"^fm_cd:"))
    app.add_handler(CallbackQueryHandler(cb_fm_file,            pattern=r"^fm_file:"))
    app.add_handler(CallbackQueryHandler(cb_fm_download,        pattern=r"^fm_download:"))
    app.add_handler(CallbackQueryHandler(cb_fm_del_confirm,     pattern=r"^fm_del_confirm:"))
    app.add_handler(CallbackQueryHandler(cb_fm_delete,          pattern=r"^fm_delete:"))
    app.add_handler(CallbackQueryHandler(cb_fm_zipdir,          pattern=r"^fm_zipdir:"))
    app.add_handler(CallbackQueryHandler(cb_fm_search,          pattern=r"^fm_search:"))

    # ── Git callbacks ─────────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(cb_bot_git,            pattern=r"^bot_git:"))
    app.add_handler(CallbackQueryHandler(cb_git_pull,           pattern=r"^git_pull:"))
    app.add_handler(CallbackQueryHandler(cb_git_autoreq,        pattern=r"^git_autoreq:"))
    app.add_handler(CallbackQueryHandler(cb_git_check,          pattern=r"^git_check:"))
    app.add_handler(CallbackQueryHandler(cb_git_toggle_auto,    pattern=r"^git_toggle_auto:"))

    # ── Advanced callbacks ────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(cb_bot_uptime,         pattern=r"^bot_uptime:"))
    app.add_handler(CallbackQueryHandler(cb_bot_notes,          pattern=r"^bot_notes:"))
    app.add_handler(CallbackQueryHandler(cb_note_edit_start,    pattern=r"^note_edit:"))
    app.add_handler(CallbackQueryHandler(cb_bot_clone_start,    pattern=r"^bot_clone:"))
    app.add_handler(CallbackQueryHandler(cb_bot_rules,          pattern=r"^bot_rules:"))
    app.add_handler(CallbackQueryHandler(cb_rule_add_start,     pattern=r"^rule_add:"))
    app.add_handler(CallbackQueryHandler(cb_rule_clear,         pattern=r"^rule_clear:"))
    app.add_handler(CallbackQueryHandler(cb_bot_integrity,      pattern=r"^bot_integrity:"))
    app.add_handler(CallbackQueryHandler(cb_integrity_record,   pattern=r"^integrity_record:"))

    # ── Global /reset — clears stuck conversations + reply keyboard ─────────
    from telegram import ReplyKeyboardRemove
    from telegram.ext import ContextTypes, ConversationHandler
    from keyboards.main import main_menu

    async def _reset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        uid = update.effective_user.id
        for mod_name, ctx_name in [
            ("handlers.upload",       "_upload_ctx"),
            ("handlers.env_vars",     "_env_ctx"),
            ("handlers.git_handler",  "_git_ctx"),
            ("handlers.file_manager", "_fm_ctx"),
            ("handlers.advanced",     "_clone_ctx"),
        ]:
            try:
                mod = __import__(mod_name, fromlist=[ctx_name])
                d = getattr(mod, ctx_name, None)
                if isinstance(d, dict):
                    d.pop(uid, None)
            except Exception:
                pass
        try:
            await update.message.reply_text(
                "🔄 Reset complete. All conversations cleared.",
                reply_markup=ReplyKeyboardRemove(),
            )
        except Exception:
            pass
        try:
            await update.message.reply_text(
                "🏠 Main Menu:",
                reply_markup=main_menu(),
            )
        except Exception:
            pass
        return ConversationHandler.END

    app.add_handler(CommandHandler("reset", _reset_cmd), group=999)

    async def _global_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        return await _reset_cmd(update, ctx)

    app.add_handler(
        MessageHandler(filters.Regex("^❌ Cancel$"), _global_cancel),
        group=999,
    )

    return app


def _placeholder(text: str):
    async def _h(update: Update, ctx) -> None:
        await update.message.reply_text(text)
    return _h


async def _send_report(update: Update, ctx) -> None:
    from services.daily_report import generate_daily_report
    text = await generate_daily_report()
    await update.message.reply_text(text, parse_mode="HTML")


async def _cb_cancel(update: Update, ctx) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Cancelled.")


def main() -> None:
    global _shutdown_event
    
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _setup_signal_handlers()

    async def _run():
        global _shutdown_event
        _shutdown_event = asyncio.Event()
        
        await init_db()
        app = build_app()
        
        async with app:
            await app.start()
            await app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
            logger.info("Polling started.")
            
            # সিগন্যাল না আসা পর্যন্ত অপেক্ষা করবে
            await _shutdown_event.wait()
            
            logger.info("Shutting down gracefully...")
            await app.updater.stop()
            await app.stop()

    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Manager stopped.")
    finally:
        # মেইন লুপ বন্ধ হওয়ার পরেই ক্লিনআপ চলবে, তাই আর এরর হবে না
        _sync_stop_all_bots()


if __name__ == "__main__":
    main()
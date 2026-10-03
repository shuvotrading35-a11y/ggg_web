from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import BotState, STATE_EMOJI
from database import Bot


def bot_list_kb(bots: list[Bot]) -> InlineKeyboardMarkup:
    rows = []
    for bot in bots:
        emoji = STATE_EMOJI.get(bot.state, "❓")
        rows.append([InlineKeyboardButton(
            f"{emoji} {bot.name}",
            callback_data=f"bot_detail:{bot.id}",
        )])
    return InlineKeyboardMarkup(rows)


def bot_detail_kb(bot: Bot) -> InlineKeyboardMarkup:
    is_running = bot.state == BotState.RUNNING

    row_control = []
    if is_running:
        row_control += [
            InlineKeyboardButton("⏹ Stop",    callback_data=f"bot_stop:{bot.id}"),
            InlineKeyboardButton("🔄 Restart", callback_data=f"bot_restart:{bot.id}"),
        ]
    else:
        row_control += [
            InlineKeyboardButton("▶️ Start", callback_data=f"bot_start:{bot.id}"),
        ]

    return InlineKeyboardMarkup([
        row_control,
        [
            InlineKeyboardButton("📜 Logs",        callback_data=f"bot_logs:{bot.id}"),
            InlineKeyboardButton("⚙️ Env Vars",    callback_data=f"bot_env:{bot.id}"),
            InlineKeyboardButton("📊 Resources",   callback_data=f"bot_res:{bot.id}"),
        ],
        [
            InlineKeyboardButton("📦 Backup",      callback_data=f"bot_backup:{bot.id}"),
            InlineKeyboardButton("🔄 Versions",    callback_data=f"bot_versions:{bot.id}"),
            InlineKeyboardButton("🏓 Ping",        callback_data=f"bot_ping:{bot.id}"),
        ],
        [
            InlineKeyboardButton("⏰ Schedule",    callback_data=f"bot_schedule:{bot.id}"),
            InlineKeyboardButton("❤️ Health",      callback_data=f"bot_health:{bot.id}"),
            InlineKeyboardButton("📤 Export",      callback_data=f"bot_export:{bot.id}"),
        ],
        [
            InlineKeyboardButton("🗂 File Manager", callback_data=f"fm_open:{bot.id}"),
        ],
        [
            InlineKeyboardButton("🗑 Delete",      callback_data=f"bot_delete_confirm:{bot.id}"),
            InlineKeyboardButton("⬅️ Back",        callback_data="bot_list"),
        ],
    ])


def logs_kb(bot_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh",   callback_data=f"bot_logs:{bot_id}"),
            InlineKeyboardButton("📥 Download",  callback_data=f"bot_logs_dl:{bot_id}"),
            InlineKeyboardButton("🧹 Clear",     callback_data=f"bot_logs_clear:{bot_id}"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"bot_detail:{bot_id}")],
    ])


def env_kb(bot_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Variable",    callback_data=f"env_add:{bot_id}"),
            InlineKeyboardButton("📋 View Variables",  callback_data=f"env_view:{bot_id}"),
        ],
        [
            InlineKeyboardButton("🗑 Delete Variable", callback_data=f"env_del:{bot_id}"),
            InlineKeyboardButton("⬅️ Back",            callback_data=f"bot_detail:{bot_id}"),
        ],
    ])


def versions_kb(bot_id: int, versions: list) -> InlineKeyboardMarkup:
    rows = []
    for v in versions:
        rows.append([InlineKeyboardButton(
            f"v{v.version_num} — {v.created_at.strftime('%Y-%m-%d %H:%M')}",
            callback_data=f"bot_rollback:{bot_id}:{v.id}",
        )])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"bot_detail:{bot_id}")])
    return InlineKeyboardMarkup(rows)


def bulk_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ Start All",   callback_data="bulk_start"),
            InlineKeyboardButton("⏹ Stop All",    callback_data="bulk_stop"),
            InlineKeyboardButton("🔄 Restart All", callback_data="bulk_restart"),
        ],
        [
            InlineKeyboardButton("📦 Backup All",      callback_data="bulk_backup"),
            InlineKeyboardButton("🧹 Clear All Logs",  callback_data="bulk_clear_logs"),
        ],
    ])

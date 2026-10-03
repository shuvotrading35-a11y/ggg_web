from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram._utils.types import JSONDict

from config import BotState, STATE_EMOJI
from database import Bot


# ══════════════════════════════════════════════════════════════════
# Styled button
# ══════════════════════════════════════════════════════════════════
# NOTE: move to keyboards/style.py and import everywhere.

class StyledButton(InlineKeyboardButton):
    """InlineKeyboardButton with an optional `style` field."""

    __slots__ = ("_style",)

    def __init__(self, text: str, style: Optional[str] = None, **kwargs):
        super().__init__(text=text, **kwargs)
        object.__setattr__(self, "_style", style)

    def to_dict(self, recursive: bool = True) -> JSONDict:
        data = super().to_dict(recursive=recursive)
        if self._style:
            data["style"] = self._style
        return data


def bot_list_kb(bots: list[Bot]) -> InlineKeyboardMarkup:
    rows = []
    for bot in bots:
        emoji = STATE_EMOJI.get(bot.state, "❓")
        rows.append([StyledButton(
            f"{emoji} {bot.name}",
            style="primary",
            callback_data=f"bot_detail:{bot.id}",
        )])
    return InlineKeyboardMarkup(rows)


def bot_detail_kb(bot: Bot) -> InlineKeyboardMarkup:
    is_running = bot.state == BotState.RUNNING

    row_control = []
    if is_running:
        row_control += [
            StyledButton("⏹ Stop",    style="danger",  callback_data=f"bot_stop:{bot.id}"),
            StyledButton("🔄 Restart", style="primary", callback_data=f"bot_restart:{bot.id}"),
        ]
    else:
        row_control += [
            StyledButton("▶️ Start", style="success", callback_data=f"bot_start:{bot.id}"),
        ]

    return InlineKeyboardMarkup([
        row_control,
        [
            StyledButton("📜 Logs",      style="primary", callback_data=f"bot_logs:{bot.id}"),
            StyledButton("⚙️ Env Vars",  style="primary", callback_data=f"bot_env:{bot.id}"),
            StyledButton("📊 Resources", style="primary", callback_data=f"bot_res:{bot.id}"),
        ],
        [
            StyledButton("📦 Backup",   style="primary", callback_data=f"bot_backup:{bot.id}"),
            StyledButton("🔄 Versions", style="primary", callback_data=f"bot_versions:{bot.id}"),
            StyledButton("🏓 Ping",     style="primary", callback_data=f"bot_ping:{bot.id}"),
        ],
        [
            StyledButton("⏰ Schedule", style="primary", callback_data=f"bot_schedule:{bot.id}"),
            StyledButton("❤️ Health",   style="primary", callback_data=f"bot_health:{bot.id}"),
            StyledButton("📤 Export",   style="primary", callback_data=f"bot_export:{bot.id}"),
        ],
        [
            StyledButton("🗂 File Manager", style="primary", callback_data=f"fm_open:{bot.id}"),
        ],
        [
            StyledButton("🗑 Delete", style="danger",  callback_data=f"bot_delete_confirm:{bot.id}"),
            StyledButton("⬅️ Back",   style="primary", callback_data="bot_list"),
        ],
    ])


def logs_kb(bot_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            StyledButton("🔄 Refresh",  style="primary", callback_data=f"bot_logs:{bot_id}"),
            StyledButton("📥 Download", style="success", callback_data=f"bot_logs_dl:{bot_id}"),
            StyledButton("🧹 Clear",    style="danger",  callback_data=f"bot_logs_clear:{bot_id}"),
        ],
        [StyledButton("⬅️ Back", style="primary", callback_data=f"bot_detail:{bot_id}")],
    ])


def env_kb(bot_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            StyledButton("➕ Add Variable",   style="success", callback_data=f"env_add:{bot_id}"),
            StyledButton("📋 View Variables", style="primary", callback_data=f"env_view:{bot_id}"),
        ],
        [
            StyledButton("🗑 Delete Variable", style="danger",  callback_data=f"env_del:{bot_id}"),
            StyledButton("⬅️ Back",            style="primary", callback_data=f"bot_detail:{bot_id}"),
        ],
    ])


def versions_kb(bot_id: int, versions: list) -> InlineKeyboardMarkup:
    rows = []
    for v in versions:
        rows.append([StyledButton(
            f"v{v.version_num} — {v.created_at.strftime('%Y-%m-%d %H:%M')}",
            style="primary",
            callback_data=f"bot_rollback:{bot_id}:{v.id}",
        )])
    rows.append([StyledButton("⬅️ Back", style="primary", callback_data=f"bot_detail:{bot_id}")])
    return InlineKeyboardMarkup(rows)


def bulk_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            StyledButton("▶️ Start All",   style="success", callback_data="bulk_start"),
            StyledButton("⏹ Stop All",    style="danger",  callback_data="bulk_stop"),
            StyledButton("🔄 Restart All", style="primary", callback_data="bulk_restart"),
        ],
        [
            StyledButton("📦 Backup All",     style="primary", callback_data="bulk_backup"),
            StyledButton("🧹 Clear All Logs", style="danger",  callback_data="bulk_clear_logs"),
        ],
    ])
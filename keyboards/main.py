from typing import Optional

from telegram import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram._utils.types import JSONDict


# ══════════════════════════════════════════════════════════════════
# Styled buttons
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


class StyledKeyboardButton(KeyboardButton):
    """KeyboardButton with an optional `style` field for reply keyboards."""

    __slots__ = ("_style",)

    def __init__(self, text: str, style: Optional[str] = None, **kwargs):
        super().__init__(text=text, **kwargs)
        object.__setattr__(self, "_style", style)

    def to_dict(self, recursive: bool = True) -> JSONDict:
        data = super().to_dict(recursive=recursive)
        if self._style:
            data["style"] = self._style
        return data


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [
                StyledKeyboardButton("🤖 My Bots",     style="primary"),
                StyledKeyboardButton("➕ Add Bot",     style="success"),
            ],
            [
                StyledKeyboardButton("📊 Server Stats", style="primary"),
                StyledKeyboardButton("⚙️ Settings",     style="primary"),
            ],
            [
                StyledKeyboardButton("📜 System Logs", style="primary"),
                StyledKeyboardButton("🔐 Token Vault", style="primary"),
            ],
            [
                StyledKeyboardButton("⚡ Bulk Actions", style="danger"),
                StyledKeyboardButton("⏰ Schedules",    style="primary"),
            ],
            [
                StyledKeyboardButton("💻 Terminal",     style="danger"),
                StyledKeyboardButton("🔌 Port Manager", style="primary"),
            ],
            [
                StyledKeyboardButton("📊 Daily Report", style="primary"),
                StyledKeyboardButton("🔄 Refresh",      style="primary"),
            ],
            [
                StyledKeyboardButton("ℹ️ Help", style="primary"),
            ],
        ],
        resize_keyboard=True,
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[StyledKeyboardButton("❌ Cancel", style="danger")]],
        resize_keyboard=True,
    )


def confirm_kb(action: str, target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            StyledButton("❌ Cancel",  style="success", callback_data="cancel"),
            StyledButton("✅ Confirm", style="danger",  callback_data=f"{action}:{target_id}"),
        ]
    ])
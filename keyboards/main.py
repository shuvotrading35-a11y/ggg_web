from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["🤖 My Bots", "➕ Add Bot"],
            ["📊 Server Stats", "⚙️ Settings"],
            ["📜 System Logs", "🔐 Token Vault"],
            ["⚡ Bulk Actions", "⏰ Schedules"],
            ["🔄 Refresh", "ℹ️ Help"],
        ],
        resize_keyboard=True,
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["❌ Cancel"]], resize_keyboard=True)


def confirm_kb(action: str, target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("❌ Cancel",  callback_data=f"cancel"),
            InlineKeyboardButton("✅ Confirm", callback_data=f"{action}:{target_id}"),
        ]
    ])

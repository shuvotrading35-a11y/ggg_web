"""
handlers/advanced.py — Uptime dashboard, bot notes, cloner, restart rules,
                        file integrity, port manager, dependency checker.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)

from database import AsyncSessionLocal, Bot, BotNote, RestartRule
from keyboards.main import cancel_kb, main_menu
from services.audit import log_action
from services.bot_cloner import clone_bot
from services.file_integrity import check_integrity, record_integrity
from services.port_manager import detect_port_conflicts, get_all_port_map
from services.uptime_tracker import get_uptime_summary, get_monthly_uptime
from utils.security import is_admin
from sqlalchemy import select

# ── Conversation states ───────────────────────────────────────────────────────
WAIT_NOTE, WAIT_CLONE_NAME, WAIT_RULE_CONDITION, WAIT_RULE_THRESHOLD = range(4)
_ctx: dict[int, dict] = {}


# ═══════════════════════════════════════════════════════════════════════════════
# UPTIME DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

async def cb_bot_uptime(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        await query.edit_message_text("❌ Bot not found.")
        return

    records    = await get_uptime_summary(bot_id, days=7)
    monthly    = await get_monthly_uptime(bot_id)
    month_icon = "✅" if monthly >= 95 else ("⚠️" if monthly >= 80 else "❌")

    lines = [
        f"📈 <b>Uptime Dashboard — {bot.name}</b>\n",
        f"{month_icon} Monthly average: <b>{monthly:.1f}%</b>\n",
        "<b>Last 7 days:</b>",
    ]
    for r in records:
        bar  = _uptime_bar(r["uptime_pct"])
        icon = "✅" if r["uptime_pct"] >= 95 else ("⚠️" if r["uptime_pct"] >= 80 else "❌")
        lines.append(f"{icon} {r['date']}  {bar}  {r['uptime_pct']:.1f}%")
        if r["crash_count"]:
            lines[-1] += f"  💥{r['crash_count']}"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data=f"bot_uptime:{bot_id}"),
        InlineKeyboardButton("⬅️ Back",    callback_data=f"bot_detail:{bot_id}"),
    ]])
    await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)


def _uptime_bar(pct: float, w: int = 8) -> str:
    filled = int(pct / 100 * w)
    return "█" * filled + "░" * (w - filled)


# ═══════════════════════════════════════════════════════════════════════════════
# BOT NOTES
# ═══════════════════════════════════════════════════════════════════════════════

async def cb_bot_notes(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot    = await s.get(Bot, bot_id)
        result = await s.execute(select(BotNote).where(BotNote.bot_id == bot_id))
        note   = result.scalars().first()

    text = (
        f"📝 <b>Notes — {bot.name}</b>\n\n"
        + (note.content if note else "<i>No notes yet.</i>")
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit Note", callback_data=f"note_edit:{bot_id}")],
        [InlineKeyboardButton("⬅️ Back",      callback_data=f"bot_detail:{bot_id}")],
    ])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)


async def cb_note_edit_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query   = update.callback_query
    await query.answer()
    bot_id  = int(query.data.split(":")[1])
    user_id = update.effective_user.id

    _ctx[user_id] = {"bot_id": bot_id, "action": "note"}
    await query.message.reply_text(
        "📝 Enter your note for this bot:",
        reply_markup=cancel_kb(),
    )
    return WAIT_NOTE


async def receive_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    data    = _ctx.pop(user_id, {})
    bot_id  = data.get("bot_id")
    content = update.message.text.strip()

    async with AsyncSessionLocal() as s:
        result = await s.execute(select(BotNote).where(BotNote.bot_id == bot_id))
        note   = result.scalars().first()
        if note:
            note.content = content
        else:
            s.add(BotNote(bot_id=bot_id, content=content))
        await s.commit()

    await update.message.reply_text("✅ Note saved.", reply_markup=main_menu())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════════
# BOT CLONER
# ═══════════════════════════════════════════════════════════════════════════════

async def cb_bot_clone_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query   = update.callback_query
    await query.answer()
    bot_id  = int(query.data.split(":")[1])
    user_id = update.effective_user.id

    _ctx[user_id] = {"bot_id": bot_id, "action": "clone"}
    await query.message.reply_text(
        "🔁 Enter a <b>name</b> for the cloned bot:",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    return WAIT_CLONE_NAME


async def receive_clone_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id   = update.effective_user.id
    data      = _ctx.pop(user_id, {})
    source_id = data.get("bot_id")
    new_name  = update.message.text.strip()

    msg = await update.message.reply_text(f"⏳ Cloning bot…")
    ok, result, new_id = await clone_bot(source_id, new_name)
    await msg.edit_text(result, parse_mode="HTML")

    if ok:
        await log_action(f"Cloned bot {source_id} → {new_name}", bot_name=new_name)

    await update.message.reply_text("Done.", reply_markup=main_menu())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════════
# RESTART RULES
# ═══════════════════════════════════════════════════════════════════════════════

RULE_CONDITIONS = {
    "cpu_gt":         ("🔥 CPU > X%",          "Enter CPU % threshold (e.g. 80):"),
    "ram_gt":         ("💾 RAM > X MB",         "Enter RAM threshold in MB (e.g. 500):"),
    "uptime_gt":      ("⏱ Uptime > X hours",   "Enter uptime threshold in hours (e.g. 168):"),
    "error_count_gt": ("💥 Restarts > X",       "Enter restart count threshold (e.g. 10):"),
}


async def cb_bot_rules(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot    = await s.get(Bot, bot_id)
        result = await s.execute(select(RestartRule).where(RestartRule.bot_id == bot_id))
        rules  = result.scalars().all()

    lines = [f"⚡ <b>Restart Rules — {bot.name}</b>\n"]
    for r in rules:
        icon  = "✅" if r.enabled else "☐"
        label = RULE_CONDITIONS.get(r.condition, (r.condition,))[0]
        val   = int(r.threshold) if r.condition in ("ram_gt", "error_count_gt") else r.threshold
        lines.append(f"{icon} {label.replace('X', str(val))}")

    if not rules:
        lines.append("<i>No rules set.</i>")

    rows = []
    for cond, (label, _) in RULE_CONDITIONS.items():
        rows.append([InlineKeyboardButton(
            f"➕ {label}", callback_data=f"rule_add:{bot_id}:{cond}"
        )])
    if rules:
        rows.append([InlineKeyboardButton("🗑 Clear All Rules", callback_data=f"rule_clear:{bot_id}")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"bot_detail:{bot_id}")])

    await query.edit_message_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def cb_rule_add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query    = update.callback_query
    await query.answer()
    parts    = query.data.split(":")
    bot_id   = int(parts[1])
    condition = parts[2]
    user_id  = update.effective_user.id

    _ctx[user_id] = {"bot_id": bot_id, "condition": condition, "action": "rule"}
    prompt = RULE_CONDITIONS.get(condition, ("", "Enter threshold:"))[1]
    await query.message.reply_text(prompt, reply_markup=cancel_kb())
    return WAIT_RULE_THRESHOLD


async def receive_rule_threshold(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id   = update.effective_user.id
    data      = _ctx.pop(user_id, {})
    bot_id    = data.get("bot_id")
    condition = data.get("condition")

    try:
        threshold = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid number.")
        return ConversationHandler.END

    async with AsyncSessionLocal() as s:
        s.add(RestartRule(bot_id=bot_id, condition=condition, threshold=threshold))
        await s.commit()

    label = RULE_CONDITIONS.get(condition, (condition,))[0]
    await update.message.reply_text(
        f"✅ Rule added: {label.replace('X', str(threshold))}",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


async def cb_rule_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    from sqlalchemy import delete
    async with AsyncSessionLocal() as s:
        await s.execute(delete(RestartRule).where(RestartRule.bot_id == bot_id))
        await s.commit()
    await query.edit_message_text("🗑 All rules cleared.")


# ═══════════════════════════════════════════════════════════════════════════════
# FILE INTEGRITY
# ═══════════════════════════════════════════════════════════════════════════════

async def cb_bot_integrity(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Checking integrity…")
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        return

    changed = await check_integrity(bot_id)

    if not changed:
        text = f"🔒 <b>File Integrity — {bot.name}</b>\n\n✅ All files unchanged."
    else:
        lines = [f"⚠️ <b>File Integrity — {bot.name}</b>\n", "Changed files:"]
        for item in changed:
            icon = "🗑" if item["status"] == "deleted" else "✏️"
            lines.append(f"{icon} <code>{item['file']}</code> — {item['status']}")
        text = "\n".join(lines)

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📸 Record Snapshot", callback_data=f"integrity_record:{bot_id}"),
            InlineKeyboardButton("⬅️ Back",            callback_data=f"bot_detail:{bot_id}"),
        ]
    ])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)


async def cb_integrity_record(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Recording…")
    bot_id = int(query.data.split(":")[1])

    count = await record_integrity(bot_id)
    await query.edit_message_text(
        f"📸 Snapshot recorded for {count} file(s).",
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PORT MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

async def show_port_manager(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return

    port_map   = await get_all_port_map()
    conflicts  = await detect_port_conflicts()

    lines = ["🔌 <b>Port Manager</b>\n"]

    if port_map:
        lines.append("<b>Active ports:</b>")
        for bot_name, ports in port_map.items():
            port_str = ", ".join(str(p) for p in ports)
            lines.append(f"• <b>{bot_name}</b>: <code>{port_str}</code>")
    else:
        lines.append("No active port bindings detected.")

    if conflicts:
        lines.append("\n⚠️ <b>Conflicts:</b>")
        for b1, b2, port in conflicts:
            lines.append(f"• Port <code>{port}</code>: {b1} ↔ {b2}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=main_menu())


# ═══════════════════════════════════════════════════════════════════════════════
# DEPENDENCY CHECKER
# ═══════════════════════════════════════════════════════════════════════════════

async def cb_bot_deps(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Checking dependencies…")
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        return

    from pathlib import Path
    req_file = Path(bot.directory) / "requirements.txt"
    if not req_file.exists():
        await query.edit_message_text("❌ No requirements.txt found.")
        return

    await query.edit_message_text("⏳ Checking for outdated packages…")

    import asyncio
    venv_pip = str(Path(bot.directory) / "venv" / "bin" / "pip")
    proc = await asyncio.create_subprocess_exec(
        venv_pip, "list", "--outdated", "--format=columns",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode(errors="replace").strip()

    if not output or "Package" not in output:
        text = f"✅ <b>Dependencies — {bot.name}</b>\n\nAll packages are up to date!"
    else:
        lines = output.splitlines()
        text  = f"📦 <b>Outdated Packages — {bot.name}</b>\n\n<pre>{chr(10).join(lines[:20])}</pre>"

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Update All", callback_data=f"deps_update:{bot_id}"),
            InlineKeyboardButton("⬅️ Back",       callback_data=f"bot_detail:{bot_id}"),
        ]
    ])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)


async def cb_deps_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Updating…")
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        return

    from pathlib import Path
    import asyncio

    venv_pip = str(Path(bot.directory) / "venv" / "bin" / "pip")
    req_file = str(Path(bot.directory) / "requirements.txt")

    await query.edit_message_text("⏳ Updating all packages…")

    proc = await asyncio.create_subprocess_exec(
        venv_pip, "install", "--upgrade", "-r", req_file,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode(errors="replace").strip()

    if proc.returncode == 0:
        text = f"✅ <b>All packages updated!</b>\n\n<pre>{output[-500:]}</pre>"
    else:
        text = f"❌ <b>Update failed</b>\n\n<pre>{output[-500:]}</pre>"

    await query.edit_message_text(text, parse_mode="HTML")
    await log_action(f"Updated dependencies: {bot.name}", bot_name=bot.name)


# ═══════════════════════════════════════════════════════════════════════════════
# CONVERSATIONS
# ═══════════════════════════════════════════════════════════════════════════════

async def adv_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    _ctx.pop(update.effective_user.id, None)
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu())
    return ConversationHandler.END


def note_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_note_edit_start, pattern=r"^note_edit:")],
        states={WAIT_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_note)]},
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), adv_cancel),
            CommandHandler("cancel", adv_cancel),
        ],
        per_message=False,
    )


def clone_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_bot_clone_start, pattern=r"^bot_clone:")],
        states={WAIT_CLONE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_clone_name)]},
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), adv_cancel),
            CommandHandler("cancel", adv_cancel),
        ],
        per_message=False,
    )


def rule_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_rule_add_start, pattern=r"^rule_add:")],
        states={WAIT_RULE_THRESHOLD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_rule_threshold)]},
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Cancel$"), adv_cancel),
            CommandHandler("cancel", adv_cancel),
        ],
        per_message=False,
    )

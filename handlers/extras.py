"""
handlers/extras.py — Schedule, Health, Ping, Deps handlers.

Fixes the 4 "dead" buttons on the bot detail page.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes
from sqlalchemy import select

from database import AsyncSessionLocal, Bot, HealthConfig, ScheduledTask
from keyboards.bots import bot_detail_kb


# ═══════════════════════════════════════════════════════════════════════════
# 1. PING — measure latency to Telegram API
# ═══════════════════════════════════════════════════════════════════════════

async def cb_bot_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Pinging…")
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        await query.edit_message_text("❌ Bot not found.")
        return

    # Measure Telegram API latency (3 tries)
    results = []
    for _ in range(3):
        try:
            t0 = time.perf_counter()
            await ctx.bot.get_me()
            ms = (time.perf_counter() - t0) * 1000
            results.append(ms)
        except Exception as e:
            results.append(f"err: {e}")
        await asyncio.sleep(0.2)

    # Bot process alive?
    proc_alive = False
    cpu = 0.0
    ram = 0.0
    if bot.pid:
        try:
            p = psutil.Process(bot.pid)
            proc_alive = p.is_running()
            cpu = p.cpu_percent(interval=0.1)
            ram = p.memory_info().rss / (1024 * 1024)
        except Exception:
            pass

    # Format
    ping_lines = []
    numeric = [r for r in results if isinstance(r, (int, float))]
    if numeric:
        avg = sum(numeric) / len(numeric)
        ping_lines.append(f"📡 Telegram API: <b>{avg:.0f} ms</b>")
        ping_lines.append(f"    min {min(numeric):.0f} · max {max(numeric):.0f}")
    else:
        ping_lines.append("📡 Telegram API: ❌ unreachable")

    status_emoji = "🟢" if bot.state == "running" else "🔴"
    proc_status = "🟢 alive" if proc_alive else "🔴 dead"

    text = (
        f"🏓 <b>Ping — {bot.name}</b>\n\n"
        f"Bot state: {status_emoji} <b>{bot.state}</b>\n"
        f"Process: {proc_status} (PID {bot.pid or '—'})\n"
        f"CPU: {cpu:.1f}%   RAM: {ram:.1f} MB\n\n"
        + "\n".join(ping_lines)
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 Ping Again", callback_data=f"bot_ping:{bot_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"bot_detail:{bot_id}")],
    ])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)


# ═══════════════════════════════════════════════════════════════════════════
# 2. DEPS — show requirements.txt + installed packages
# ═══════════════════════════════════════════════════════════════════════════

async def cb_bot_deps(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        await query.edit_message_text("❌ Bot not found.")
        return

    bot_dir = Path(bot.directory)
    req = bot_dir / "requirements.txt"

    if req.exists():
        try:
            content = req.read_text(errors="replace")
            lines = [
                l.strip() for l in content.splitlines()
                if l.strip() and not l.strip().startswith("#")
            ]
        except Exception as e:
            lines = [f"⚠️ read error: {e}"]
    else:
        lines = ["(no requirements.txt)"]

    # Show first 25 lines
    shown = lines[:25]
    more = "" if len(lines) <= 25 else f"\n… +{len(lines) - 25} more"

    safe = "\n".join(shown)
    if len(safe) > 3000:
        safe = safe[:3000] + "\n…"

    text = (
        f"📦 <b>Dependencies — {bot.name}</b>\n\n"
        f"📄 File: <code>{req}</code>\n"
        f"📊 {len(lines)} package(s)\n\n"
        f"<pre>{safe}</pre>"
        f"{more}"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Re-install", callback_data=f"deps_update:{bot_id}")],
        [InlineKeyboardButton("🔍 Auto-Scan Imports", callback_data=f"git_autoreq:{bot_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data=f"bot_detail:{bot_id}")],
    ])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)


async def cb_deps_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Installing…")
    bot_id = int(query.data.split(":")[1])

    await query.edit_message_text("⏳ Re-installing dependencies…")

    from services.installer import install_requirements

    async def progress(m: str):
        try:
            await query.edit_message_text(m, parse_mode="HTML")
        except Exception:
            pass

    ok, msg = await install_requirements(bot_id, progress)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back to Deps", callback_data=f"bot_deps:{bot_id}")],
        [InlineKeyboardButton("🏠 Bot Detail",   callback_data=f"bot_detail:{bot_id}")],
    ])
    try:
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)


# ═══════════════════════════════════════════════════════════════════════════
# 3. HEALTH — show health config + run a check
# ═══════════════════════════════════════════════════════════════════════════

async def cb_bot_health(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        hc_res = await s.execute(
            select(HealthConfig).where(HealthConfig.bot_id == bot_id)
        )
        hc = hc_res.scalars().first()

    if not bot:
        await query.edit_message_text("❌ Bot not found.")
        return

    # Live metrics
    alive = False
    cpu = 0.0
    ram = 0.0
    uptime_str = "—"
    if bot.pid:
        try:
            p = psutil.Process(bot.pid)
            alive = p.is_running()
            cpu = p.cpu_percent(interval=0.1)
            ram = p.memory_info().rss / (1024 * 1024)
            if alive and bot.started_at:
                secs = (datetime.now(timezone.utc) - bot.started_at).total_seconds()
                h, rem = divmod(int(secs), 3600)
                m, s = divmod(rem, 60)
                uptime_str = f"{h}h {m}m {s}s"
        except Exception:
            pass

    lines = [
        f"❤️ <b>Health — {bot.name}</b>\n",
        f"State: {'🟢' if bot.state == 'running' else '🔴'} <b>{bot.state}</b>",
        f"Process: {'🟢 alive' if alive else '🔴 dead'}",
        f"Uptime: {uptime_str}",
        f"Restarts: {bot.restart_count}",
        f"Crash loop: {'⚠️ YES' if bot.crash_loop else '✅ No'}",
        f"CPU: {cpu:.1f}%   RAM: {ram:.1f} MB",
    ]

    if bot.last_error:
        lines.append(f"\n⚠️ <b>Last error:</b>\n<pre>{(bot.last_error or '')[:300]}</pre>")

    if hc:
        lines.append("\n<b>Auto Health Config</b>")
        lines.append(f"Enabled: {'✅' if hc.enabled else '❌'}")
        lines.append(f"Type: <code>{hc.check_type}</code>")
        if hc.endpoint:
            lines.append(f"Endpoint: <code>{hc.endpoint}</code>")
        lines.append(f"Interval: {hc.interval_minutes} min")
        if hc.last_check:
            lines.append(f"Last check: {hc.last_check.strftime('%Y-%m-%d %H:%M')}")
        if hc.last_status:
            lines.append(f"Last status: <b>{hc.last_status}</b>")
    else:
        lines.append("\n<i>(no auto-health config)</i>")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 Re-check", callback_data=f"bot_health:{bot_id}")],
        [InlineKeyboardButton("⬅️ Back",     callback_data=f"bot_detail:{bot_id}")],
    ])
    await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)


# ═══════════════════════════════════════════════════════════════════════════
# 4. SCHEDULE — show + manage scheduled tasks
# ═══════════════════════════════════════════════════════════════════════════

async def cb_bot_schedule(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        res = await s.execute(
            select(ScheduledTask).where(ScheduledTask.bot_id == bot_id)
        )
        tasks = res.scalars().all()

    if not bot:
        await query.edit_message_text("❌ Bot not found.")
        return

    if not tasks:
        text = (
            f"⏰ <b>Schedules — {bot.name}</b>\n\n"
            f"<i>No scheduled tasks yet.</i>\n\n"
            f"💡 Use <code>/schedule_add {bot_id} restart 0 3 * * *</code>\n"
            f"   to add a daily 3 AM restart."
        )
    else:
        lines = [f"⏰ <b>Schedules — {bot.name}</b>\n"]
        for t in tasks:
            flag = "✅" if t.enabled else "⏸"
            last = t.last_run.strftime("%m-%d %H:%M") if t.last_run else "—"
            desc = t.description or ""
            lines.append(
                f"{flag} <code>{t.cron_expr}</code> → <b>{t.action}</b>\n"
                f"    {desc}  (last: {last})"
            )
        text = "\n".join(lines)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Clear All", callback_data=f"schedule_clear:{bot_id}")],
        [InlineKeyboardButton("⬅️ Back",      callback_data=f"bot_detail:{bot_id}")],
    ])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)


async def cb_schedule_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer("Clearing…")
    bot_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as s:
        res = await s.execute(
            select(ScheduledTask).where(ScheduledTask.bot_id == bot_id)
        )
        for t in res.scalars().all():
            await s.delete(t)
        await s.commit()

    await query.edit_message_text(
        "🗑 All schedules cleared.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data=f"bot_schedule:{bot_id}")],
        ]),
    )


# ═══════════════════════════════════════════════════════════════════════════
# /schedule_add command — quick CLI to add a schedule
# ═══════════════════════════════════════════════════════════════════════════

async def cmd_schedule_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /schedule_add <bot_id> <action> <cron_expr> [description]

    Example: /schedule_add 1 restart "0 3 * * *" "Daily 3 AM restart"
    """
    args = ctx.args or []
    if len(args) < 3:
        await update.message.reply_text(
            "Usage:\n"
            "<code>/schedule_add &lt;bot_id&gt; &lt;action&gt; \"&lt;cron&gt;\" [desc]</code>\n\n"
            "Example:\n"
            "<code>/schedule_add 1 restart \"0 3 * * *\" Daily restart</code>",
            parse_mode="HTML",
        )
        return

    try:
        bot_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ bot_id must be a number.")
        return

    action = args[1]
    cron = args[2]
    desc = " ".join(args[3:]) if len(args) > 3 else ""

    if action not in ("restart", "start", "stop", "backup"):
        await update.message.reply_text(
            f"❌ Unknown action <code>{action}</code>.\n"
            f"Allowed: restart, start, stop, backup",
            parse_mode="HTML",
        )
        return

    async with AsyncSessionLocal() as s:
        s.add(ScheduledTask(
            bot_id=bot_id,
            action=action,
            cron_expr=cron,
            description=desc or None,
            enabled=True,
        ))
        await s.commit()

    await update.message.reply_text(
        f"✅ Schedule added:\n"
        f"• Action: <b>{action}</b>\n"
        f"• Cron: <code>{cron}</code>\n"
        f"• Desc: {desc or '—'}",
        parse_mode="HTML",
    )
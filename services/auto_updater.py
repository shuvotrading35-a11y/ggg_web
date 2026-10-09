"""
services/auto_updater.py — Auto-update + manual update logic.

- update_bot_from_github(bot_id) → check + download + replace + restart
- auto_update_loop()             → background task
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from database import AsyncSessionLocal, Bot
from services.github_api import (
    parse_github_url, get_latest_sha, download_archive, extract_archive,
)

logger = logging.getLogger(__name__)


# Files/folders preserved across an update
PRESERVE = {
    ".env", "logs", "data", "venv", ".venv",
    "__pycache__", "bot.log", "stdout.log", "stderr.log",
    "requirements.txt",       # keep user's pinned versions
    "backup", "backups",
}

# How often to check (seconds)
CHECK_INTERVAL = 600       # 10 minutes

# Skip bots not checked within this period (avoid hammering GitHub)
MIN_RECHECK = 300          # 5 minutes between checks per bot


# ═══════════════════════════════════════════════════════════════════════════
# Core update logic
# ═══════════════════════════════════════════════════════════════════════════

async def check_for_update(bot: Bot) -> tuple[bool, str, str | None]:
    """
    Returns (has_update, message, new_sha).
    Does NOT modify anything — only checks GitHub.
    """
    if not bot.github_url:
        return False, "No GitHub URL configured.", None

    parsed = parse_github_url(bot.github_url)
    if not parsed:
        return False, "Invalid GitHub URL.", None

    owner, repo = parsed
    branch = bot.github_branch or "main"

    new_sha = await get_latest_sha(owner, repo, branch)
    if not new_sha:
        return False, f"Could not fetch latest commit from {owner}/{repo}@{branch}.", None

    if new_sha == bot.last_commit_sha:
        return False, "Already up to date.", new_sha

    short_new = new_sha[:8]
    short_old = (bot.last_commit_sha or "none")[:8]
    return True, f"Update available: {short_old} → {short_new}", new_sha


async def update_bot_from_github(bot_id: int) -> tuple[bool, str]:
    """
    Full update: check → download → replace → restart → save SHA.
    """
    from services.process_manager import restart_bot, stop_bot

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        return False, "Bot not found."
    if not bot.github_url:
        return False, "No GitHub URL set for this bot."

    parsed = parse_github_url(bot.github_url)
    if not parsed:
        return False, "Invalid GitHub URL."
    owner, repo = parsed
    branch = bot.github_branch or "main"

    # 1. Get SHA
    new_sha = await get_latest_sha(owner, repo, branch)
    if not new_sha:
        return False, f"Cannot fetch SHA from {owner}/{repo}@{branch}."
    if new_sha == bot.last_commit_sha:
        return False, "Already up to date."

    # 2. Download archive
    logger.info(f"[update] {bot.name}: downloading {owner}/{repo}@{branch}")
    data = await download_archive(owner, repo, branch)
    if not data:
        return False, "Archive download failed."

    # 3. Stop bot before replacing files
    try:
        await stop_bot(bot_id)
    except Exception:
        pass

    # 4. Extract into a temp dir, then merge preserving protected paths
    bot_dir = Path(bot.directory)
    tmp_dir = bot_dir.parent / f".{bot_dir.name}_update"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if not extract_archive(data, tmp_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, "Archive extract failed."

    # Snapshot: preserve protected paths from old bot_dir
    preserved_backup = bot_dir.parent / f".{bot_dir.name}_preserve"
    if preserved_backup.exists():
        shutil.rmtree(preserved_backup, ignore_errors=True)
    preserved_backup.mkdir(parents=True, exist_ok=True)

    for name in PRESERVE:
        src = bot_dir / name
        if src.exists():
            try:
                if src.is_dir():
                    shutil.copytree(src, preserved_backup / name, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, preserved_backup / name)
            except Exception as e:
                logger.warning(f"[update] preserve {name}: {e}")

    # Replace bot_dir with extracted content
    try:
        # Wipe old bot_dir entirely
        shutil.rmtree(bot_dir, ignore_errors=True)
        # Move new contents in
        shutil.move(str(tmp_dir), str(bot_dir))
        # Restore preserved paths
        for name in PRESERVE:
            src = preserved_backup / name
            if src.exists():
                target = bot_dir / name
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target, ignore_errors=True)
                    else:
                        target.unlink()
                if src.is_dir():
                    shutil.copytree(src, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, target)
    except Exception as e:
        logger.exception(f"[update] replace failed: {e}")
        shutil.rmtree(preserved_backup, ignore_errors=True)
        return False, f"Replace failed: {e}"
    finally:
        shutil.rmtree(preserved_backup, ignore_errors=True)

    # 5. Save new SHA
    async with AsyncSessionLocal() as s:
        b = await s.get(Bot, bot_id)
        if b:
            b.last_commit_sha = new_sha
            b.last_check_at = datetime.now(timezone.utc)
            await s.commit()

    # 6. Restart bot
    try:
        await restart_bot(bot_id)
    except Exception as e:
        return False, f"Files updated, but restart failed: {e}"

    short = new_sha[:8]
    return True, f"✅ Updated to {short} and restarted."


# ═══════════════════════════════════════════════════════════════════════════
# Background loop
# ═══════════════════════════════════════════════════════════════════════════

async def auto_update_loop():
    """
    Every CHECK_INTERVAL seconds, check all bots with auto_update=True.
    Only updates bots whose SHA has changed since last_check.
    """
    logger.info("[auto_update] loop started")
    # small delay so startup is not too noisy
    await asyncio.sleep(30)

    while True:
        try:
            await _run_once()
        except Exception as e:
            logger.exception(f"[auto_update] loop error: {e}")
        await asyncio.sleep(CHECK_INTERVAL)


async def _run_once():
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Bot).where(
                Bot.auto_update == True,
                Bot.github_url.isnot(None),
            )
        )
        bots = result.scalars().all()

    for bot in bots:
        # Skip if checked recently
        if bot.last_check_at:
            try:
                delta = (now - bot.last_check_at).total_seconds()
                if delta < MIN_RECHECK:
                    continue
            except Exception:
                pass

        try:
            logger.info(f"[auto_update] checking {bot.name}…")
            ok, msg = await update_bot_from_github(bot.id)
            logger.info(f"[auto_update] {bot.name}: {msg}")

            # Save last_check_at even if no update was needed
            if not ok:
                async with AsyncSessionLocal() as s:
                    b = await s.get(Bot, bot.id)
                    if b:
                        b.last_check_at = now
                        await s.commit()
        except Exception as e:
            logger.exception(f"[auto_update] {bot.name} failed: {e}")
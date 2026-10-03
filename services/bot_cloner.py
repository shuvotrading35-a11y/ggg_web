"""
services/bot_cloner.py — Clone a hosted bot with all files, env vars, config.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from config import BotState
from database import AsyncSessionLocal, Bot, EnvVar
from utils.security import safe_slug
from sqlalchemy import select


async def clone_bot(source_bot_id: int, new_name: str) -> tuple[bool, str, int | None]:
    """
    Clone source bot into a new bot with new_name.
    Returns (success, message, new_bot_id).
    """
    new_slug = safe_slug(new_name)

    async with AsyncSessionLocal() as s:
        # Check for duplicate slug
        existing = await s.execute(select(Bot).where(Bot.slug == new_slug))
        if existing.scalars().first():
            return False, f"❌ A bot named '{new_name}' already exists.", None

        source = await s.get(Bot, source_bot_id)
        if not source:
            return False, "❌ Source bot not found.", None

        source_dir = Path(source.directory)
        new_dir    = source_dir.parent / new_slug

        # Copy files (exclude venv, logs)
        if new_dir.exists():
            shutil.rmtree(new_dir)

        shutil.copytree(
            source_dir, new_dir,
            ignore=shutil.ignore_patterns("venv", "logs", "__pycache__", "*.pyc", "versions"),
        )

        # Create new bot record
        new_bot = Bot(
            name       = new_name,
            slug       = new_slug,
            directory  = str(new_dir),
            entry_file = source.entry_file,
            state      = BotState.STOPPED,
            auto_start = False,   # Don't auto-start clone
            mode       = source.mode,
        )
        s.add(new_bot)
        await s.flush()

        # Copy env vars
        env_result = await s.execute(select(EnvVar).where(EnvVar.bot_id == source_bot_id))
        for ev in env_result.scalars().all():
            s.add(EnvVar(bot_id=new_bot.id, key=ev.key, value_enc=ev.value_enc))

        await s.commit()
        new_id = new_bot.id

    # Recreate venv and logs dirs
    (new_dir / "logs").mkdir(exist_ok=True)

    return True, f"✅ Cloned as <b>{new_name}</b>\nConfigure its .env before starting.", new_id

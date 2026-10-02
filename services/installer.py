"""
services/installer.py — Create venv and install requirements.txt for a hosted bot.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from config import BotState
from database import AsyncSessionLocal, Bot


async def install_requirements(bot_id: int, progress_cb=None) -> tuple[bool, str]:
    """
    Create venv and pip-install requirements.txt.
    progress_cb(msg) is called with status updates.
    """
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if not bot:
            return False, "Bot not found."
        bot_dir = Path(bot.directory)
        bot.state = BotState.INSTALLING
        await s.commit()

    req_file = bot_dir / "requirements.txt"
    venv_dir = bot_dir / "venv"

    if not req_file.exists():
        await _set_state(bot_id, BotState.STOPPED)
        return True, "No requirements.txt — skipping install."

    if progress_cb:
        await progress_cb("⏳ Creating virtual environment...")

    ok, out = await _run(["python3", "-m", "venv", str(venv_dir)])
    if not ok:
        await _set_state(bot_id, BotState.STOPPED)
        return False, f"❌ venv creation failed:\n<pre>{out[:800]}</pre>"

    pip = str(venv_dir / "bin" / "pip")

    if progress_cb:
        await progress_cb("⏳ Installing dependencies (this may take a moment)...")

    ok, out = await _run([pip, "install", "-r", str(req_file), "--quiet"])
    await _set_state(bot_id, BotState.STOPPED)

    if ok:
        return True, "✅ Dependencies installed successfully."
    return False, f"❌ Installation failed:\n<pre>{out[:800]}</pre>"


async def _run(cmd: list[str]) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode(errors="replace") if stdout else ""
    return proc.returncode == 0, output


async def _set_state(bot_id: int, state: str) -> None:
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if bot:
            bot.state = state
            await s.commit()

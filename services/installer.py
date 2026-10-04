"""
services/installer.py — Install dependencies for a hosted bot.

Strategy:
  • Everything is installed into the MANAGER's venv (sys.executable).
  • No per-bot venv is created. This avoids the classic
    "venv-inside-venv + --system-site-packages doesn't see the parent venv"
    problem, and guarantees child bots see every package the manager sees.

  • If requirements.txt exists → pip install -r requirements.txt
  • Otherwise                 → pip install DEFAULT_BOT_PACKAGES
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from config import BotState
from database import AsyncSessionLocal, Bot


# Packages installed when a bot has no requirements.txt
DEFAULT_BOT_PACKAGES = [
    "python-telegram-bot>=20",
    "python-dotenv",
    "aiohttp",
    "httpx",
    "requests",
    "pytz",
    "python-dateutil",
    "aiofiles",
]


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

async def install_requirements(bot_id: int, progress_cb=None) -> tuple[bool, str]:
    """
    Install bot dependencies into the manager's own venv.
    Returns (success, message).
    """
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if not bot:
            return False, "Bot not found."
        bot_dir = Path(bot.directory)
        bot.state = BotState.INSTALLING
        await s.commit()

    req_file = bot_dir / "requirements.txt"

    # If a stale per-bot venv exists from an old install, remove it so it
    # doesn't shadow the manager's venv.
    for sub in ("venv", ".venv"):
        stale = bot_dir / sub
        if stale.exists():
            try:
                import shutil
                shutil.rmtree(stale, ignore_errors=True)
            except Exception:
                pass

    pip_base = [sys.executable, "-m", "pip", "install", "--no-cache-dir"]

    # ── Case 1: no requirements.txt → install defaults ────────────────────
    if not req_file.exists():
        if progress_cb:
            await progress_cb(
                "⏳ No requirements.txt — installing default packages…\n"
                "(telegram, aiohttp, httpx, dotenv, …)"
            )
        ok, out = await _run([*pip_base, *DEFAULT_BOT_PACKAGES])
        await _set_state(bot_id, BotState.STOPPED)
        if ok:
            return True, "✅ Default packages installed."
        return False, f"❌ Default install failed:\n<pre>{out[-800:]}</pre>"

    # ── Case 2: requirements.txt → install into manager venv ──────────────
    if progress_cb:
        await progress_cb("⏳ Installing requirements.txt (this may take a while)…")

    # Upgrade pip (quiet), then install requirements
    await _run([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "--quiet"])

    ok, out = await _run([*pip_base, "-r", str(req_file)])

    # Also ensure the manager venv has the common Telegram stack,
    # because the child bot will run with sys.executable.
    if ok:
        await _run([*pip_base, "python-telegram-bot>=20", "python-dotenv"])

    await _set_state(bot_id, BotState.STOPPED)

    if ok:
        return True, "✅ Dependencies installed successfully."
    return False, f"❌ Installation failed:\n<pre>{out[-800:]}</pre>"


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

async def _run(cmd: list[str], timeout: int = 600) -> tuple[bool, str]:
    """Run a subprocess, return (success, combined_output)."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode(errors="replace") if stdout else ""
        return proc.returncode == 0, output
    except asyncio.TimeoutError:
        try:
            if proc: proc.kill()
        except Exception:
            pass
        return False, f"Command timed out after {timeout}s."
    except FileNotFoundError as e:
        return False, f"Command not found: {e}"
    except Exception as e:
        return False, f"Command failed: {e}"


async def _set_state(bot_id: int, state) -> None:
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if bot:
            bot.state = state
            await s.commit()
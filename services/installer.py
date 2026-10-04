"""
services/installer.py — Install dependencies for a hosted bot.

Strategy:
  • Everything is installed into the MANAGER's venv (sys.executable).
  • No per-bot venv is created.
  • DEFAULT_BOT_PACKAGES are ALWAYS installed, regardless of whether
    requirements.txt exists — so child bots always have telegram,
    aiohttp, httpx, requests, dotenv, etc.
  • If requirements.txt exists, its packages are installed on top.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

from config import BotState
from database import AsyncSessionLocal, Bot


# Packages installed for every bot — Telegram stack + common utilities.
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

    Order:
      1. Remove any stale per-bot venv (from old installs).
      2. Install DEFAULT_BOT_PACKAGES      ← always
      3. If requirements.txt exists:
           install -r requirements.txt    ← on top
      4. If requirements.txt does NOT exist:
           done (defaults already cover it).

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

    # ── Step 1: remove any stale per-bot venv ─────────────────────────────
    for sub in ("venv", ".venv"):
        stale = bot_dir / sub
        if stale.exists():
            try:
                shutil.rmtree(stale, ignore_errors=True)
            except Exception:
                pass

    pip_base = [sys.executable, "-m", "pip", "install", "--no-cache-dir"]

    # ── Step 2: install defaults (ALWAYS) ─────────────────────────────────
    if progress_cb:
        await progress_cb(
            "⏳ Installing base packages…\n"
            "(telegram, aiohttp, httpx, requests, dotenv, …)"
        )
    ok_defaults, out_defaults = await _run([*pip_base, *DEFAULT_BOT_PACKAGES])

    if not ok_defaults:
        await _set_state(bot_id, BotState.STOPPED)
        return (
            False,
            f"❌ Default packages install failed:\n<pre>{out_defaults[-800:]}</pre>",
        )

    # ── Step 3: if requirements.txt exists, install it on top ─────────────
    if not req_file.exists():
        await _set_state(bot_id, BotState.STOPPED)
        return True, "✅ Default packages installed (no requirements.txt)."

    if progress_cb:
        await progress_cb("⏳ Installing requirements.txt (this may take a while)…")

    # Upgrade pip (quiet), then install requirements
    await _run([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "--quiet"])
    ok_req, out_req = await _run([*pip_base, "-r", str(req_file)])

    await _set_state(bot_id, BotState.STOPPED)

    if ok_req:
        return True, "✅ Default + requirements.txt packages installed."
    return False, f"❌ Requirements install failed:\n<pre>{out_req[-800:]}</pre>"


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
            if proc:
                proc.kill()
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
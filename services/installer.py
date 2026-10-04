"""
services/installer.py — Create venv and install requirements.txt for a hosted bot.

Behavior:
  • If requirements.txt exists:
      - Create a per-bot venv WITH --system-site-packages
        (so it can see the manager's already-installed libraries)
      - pip install -r requirements.txt
  • If requirements.txt does NOT exist:
      - Install a default set of common bot packages into the manager's venv
      - No per-bot venv is created; the bot will run with sys.executable
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
    Install bot dependencies.

    - If requirements.txt exists → per-bot venv + requirements.txt
    - Otherwise → default packages into the manager's venv
    """
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if not bot:
            return False, "Bot not found."
        bot_dir = Path(bot.directory)
        bot.state = BotState.INSTALLING
        await s.commit()

    req_file = bot_dir / "requirements.txt"

    # ── Case 1: no requirements.txt → install defaults into manager venv ──
    if not req_file.exists():
        ok, msg = await _install_defaults(progress_cb)
        await _set_state(bot_id, BotState.STOPPED)
        return ok, msg

    # ── Case 2: requirements.txt → create per-bot venv + install ──
    venv_dir = bot_dir / "venv"

    # If venv already exists from a previous install, reuse it
    needs_venv = not (venv_dir / "bin" / "python").exists()

    if needs_venv:
        if progress_cb:
            await progress_cb("⏳ Creating virtual environment…")

        # --system-site-packages lets the child venv see the manager's libs
        # (python-telegram-bot, aiohttp, etc.) so requirements.txt only
        # needs to list *extra* packages.
        ok, out = await _run([
            sys.executable, "-m", "venv",
            "--system-site-packages",
            str(venv_dir),
        ])
        if not ok:
            await _set_state(bot_id, BotState.STOPPED)
            return False, f"❌ venv creation failed:\n<pre>{out[-800:]}</pre>"

    pip = str(venv_dir / "bin" / "pip")

    if progress_cb:
        await progress_cb("⏳ Installing requirements.txt (this may take a while)…")

    # Upgrade pip first (silent), then install requirements
    await _run([pip, "install", "--upgrade", "pip", "--quiet"])
    ok, out = await _run([pip, "install", "-r", str(req_file), "--quiet"])

    await _set_state(bot_id, BotState.STOPPED)

    if ok:
        return True, "✅ Dependencies installed successfully."
    return False, f"❌ Installation failed:\n<pre>{out[-800:]}</pre>"


# ═══════════════════════════════════════════════════════════════════════════
# Default packages
# ═══════════════════════════════════════════════════════════════════════════

async def _install_defaults(progress_cb=None) -> tuple[bool, str]:
    """Install DEFAULT_BOT_PACKAGES into the manager's own venv."""
    if progress_cb:
        await progress_cb(
            "⏳ No requirements.txt — installing default packages…\n"
            "(telegram, aiohttp, httpx, dotenv, …)"
        )

    ok, out = await _run([
        sys.executable, "-m", "pip", "install",
        "--no-cache-dir",
        *DEFAULT_BOT_PACKAGES,
    ])

    if ok:
        return True, "✅ Default packages installed."
    return False, f"❌ Default install failed:\n<pre>{out[-800:]}</pre>"


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

async def _run(cmd: list[str], timeout: int = 600) -> tuple[bool, str]:
    """Run a subprocess, return (success, combined_output)."""
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
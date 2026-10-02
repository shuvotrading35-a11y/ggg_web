"""
services/process_manager.py — Start / stop / restart hosted bots as subprocesses.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

from config import BotState, HOSTING_DIR
from database import AsyncSessionLocal, Bot
from sqlalchemy import select


# pid → asyncio.subprocess.Process
_processes: dict[int, asyncio.subprocess.Process] = {}


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _get_bot(bot_id: int) -> Bot | None:
    async with AsyncSessionLocal() as s:
        return await s.get(Bot, bot_id)


async def _save(bot_id: int, **kwargs) -> None:
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if bot:
            for k, v in kwargs.items():
                setattr(bot, k, v)
            await s.commit()


def _bot_dir(bot: Bot) -> Path:
    return Path(bot.directory)


def _venv_python(bot: Bot) -> str:
    venv = _bot_dir(bot) / "venv" / "bin" / "python"
    return str(venv) if venv.exists() else "python3"


def _env_file(bot: Bot) -> Path:
    return _bot_dir(bot) / ".env"


def _load_bot_env(bot: Bot) -> dict[str, str]:
    """Load decrypted env vars from DB and merge with current OS env."""
    from utils.security import decrypt
    env = os.environ.copy()
    # read from .env file in bot dir (written by env handler)
    env_path = _env_file(bot)
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


# ── Public API ────────────────────────────────────────────────────────────────

async def start_bot(bot_id: int) -> tuple[bool, str]:
    """Start a hosted bot. Returns (success, message)."""
    bot = await _get_bot(bot_id)
    if not bot:
        return False, "Bot not found."
    if bot.state == BotState.RUNNING and bot.pid and _pid_alive(bot.pid):
        return False, "Bot is already running."
    if not bot.entry_file:
        return False, "No entry file selected."

    entry = _bot_dir(bot) / bot.entry_file
    if not entry.exists():
        return False, f"Entry file not found: {bot.entry_file}"

    log_dir = _bot_dir(bot) / "logs"
    log_dir.mkdir(exist_ok=True)

    stdout_log = open(log_dir / "stdout.log", "a")
    stderr_log = open(log_dir / "stderr.log", "a")

    env = _load_bot_env(bot)
    python = _venv_python(bot)

    await _save(bot_id, state=BotState.STARTING)

    try:
        proc = await asyncio.create_subprocess_exec(
            python, str(entry),
            cwd=str(_bot_dir(bot)),
            env=env,
            stdout=stdout_log,
            stderr=stderr_log,
        )
        _processes[bot_id] = proc
        await _save(
            bot_id,
            pid=proc.pid,
            state=BotState.RUNNING,
            started_at=datetime.now(timezone.utc),
            last_error=None,
        )
        return True, f"✅ Bot started (PID {proc.pid})"
    except Exception as e:
        await _save(bot_id, state=BotState.STOPPED, pid=None, last_error=str(e))
        return False, f"❌ Failed to start: {e}"


async def stop_bot(bot_id: int) -> tuple[bool, str]:
    """Stop a running bot gracefully."""
    bot = await _get_bot(bot_id)
    if not bot:
        return False, "Bot not found."

    proc = _processes.get(bot_id)
    pid  = bot.pid

    # Try via stored process handle
    if proc and proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()

    # Fallback: kill by PID
    elif pid and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            await asyncio.sleep(5)
            if _pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    _processes.pop(bot_id, None)
    await _save(
        bot_id,
        pid=None,
        state=BotState.STOPPED,
        stopped_at=datetime.now(timezone.utc),
    )
    return True, "⏹ Bot stopped."


async def restart_bot(bot_id: int) -> tuple[bool, str]:
    await _save(bot_id, state=BotState.RESTARTING)
    ok, msg = await stop_bot(bot_id)
    await asyncio.sleep(1)
    ok2, msg2 = await start_bot(bot_id)
    return ok2, msg2


def _pid_alive(pid: int) -> bool:
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return False


async def get_process_info(bot: Bot) -> dict:
    """Return live CPU/RAM/uptime for a bot."""
    info = {"cpu": 0.0, "ram_mb": 0.0, "pid": bot.pid, "alive": False}
    if not bot.pid:
        return info
    try:
        p = psutil.Process(bot.pid)
        info["cpu"]    = p.cpu_percent(interval=0.1)
        info["ram_mb"] = p.memory_info().rss / (1024 * 1024)
        info["alive"]  = p.is_running()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return info


async def restore_auto_start_bots() -> list[str]:
    """Called on startup to re-launch bots with auto_start=True."""
    restored: list[str] = []
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Bot).where(Bot.auto_start == True, Bot.state != BotState.INSTALLING)
        )
        bots = result.scalars().all()

    for bot in bots:
        ok, _ = await start_bot(bot.id)
        if ok:
            restored.append(bot.name)
    return restored

"""
services/process_manager.py — Start / stop / restart hosted bots as subprocesses.

Key features:
  • Uses sys.executable (manager's own Python) so child bots see all deps.
  • Kills orphan processes before starting to prevent "Conflict: getUpdates".
  • Strips inline `# comments` from .env values safely.
  • Never leaves two instances of the same bot running.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

from config import BotState, HOSTING_DIR
from database import AsyncSessionLocal, Bot
from sqlalchemy import select


# bot_id → asyncio.subprocess.Process
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


def _venv_has_telegram(python_path: str) -> bool:
    """Return True if the given Python can `import telegram`."""
    try:
        r = subprocess.run(
            [python_path, "-c", "import telegram"],
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def _venv_python(bot: Bot) -> str:
    """
    Return the Python interpreter for the child bot.

    Priority:
      1. Per-bot venv (venv/ or .venv/) — ONLY if it can `import telegram`
      2. sys.executable (manager's own python — has telegram/aiohttp/etc.)
    """
    bot_dir = _bot_dir(bot)
    for sub in ("venv", ".venv"):
        candidate = bot_dir / sub / "bin" / "python"
        if candidate.exists() and _venv_has_telegram(str(candidate)):
            return str(candidate)
    return sys.executable or "python3"


def _env_file(bot: Bot) -> Path:
    return _bot_dir(bot) / ".env"


def _strip_inline_comment(value: str) -> str:
    """
    Strip inline `# comment` from an env value.

    Rules:
      • Quoted values ('...' or "...") are returned unquoted, untouched.
      • Otherwise, strips at the first ` #` or `\\t#` (whitespace + hash).
      • A bare `#` inside the value (e.g. password "a#b") is preserved.
    """
    v = value.strip()
    if len(v) >= 2 and v[0] in ("'", '"') and v[-1] == v[0]:
        return v[1:-1]

    for marker in (" #", "\t#"):
        idx = v.find(marker)
        if idx != -1:
            v = v[:idx].rstrip()
            break
    return v


def _load_bot_env(bot: Bot) -> dict[str, str]:
    """Load env vars from the bot's .env file and merge with current OS env."""
    env = os.environ.copy()
    env_path = _env_file(bot)
    if env_path.exists():
        try:
            for raw_line in env_path.read_text(errors="replace").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                # Support "export KEY=val"
                if line.startswith("export "):
                    line = line[7:].lstrip()
                if "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = _strip_inline_comment(v)
                if k:
                    env[k] = v
        except Exception:
            pass
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _kill_orphans_by_entry(entry_path: str) -> list[int]:
    """
    Kill any running process whose command line contains `entry_path`.
    Never kills the manager itself. Returns list of killed PIDs.
    """
    killed: list[int] = []
    if not entry_path or len(entry_path) < 10:
        return killed
    my_pid = os.getpid()
    try:
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                if p.pid == my_pid:
                    continue
                cmd = " ".join(p.info.get("cmdline") or [])
                if entry_path in cmd and "python" in cmd.lower():
                    p.kill()
                    killed.append(p.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:
                continue
    except Exception:
        pass
    return killed


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

    bot_dir = _bot_dir(bot)
    entry = bot_dir / bot.entry_file
    if not entry.exists():
        return False, f"Entry file not found: {bot.entry_file}"

    # ✅ Kill any orphan processes with the same entry path BEFORE starting.
    # This prevents "Conflict: terminated by other getUpdates" errors.
    try:
        killed = _kill_orphans_by_entry(str(entry.resolve()))
        if killed:
            # give the OS a moment to release the telegram long-poll
            await asyncio.sleep(2)
    except Exception:
        pass

    # Ensure runtime folders exist
    log_dir = bot_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (bot_dir / "data").mkdir(parents=True, exist_ok=True)

    stdout_log = open(log_dir / "stdout.log", "a")
    stderr_log = open(log_dir / "stderr.log", "a")

    env = _load_bot_env(bot)
    python = _venv_python(bot)

    await _save(bot_id, state=BotState.STARTING)

    try:
        proc = await asyncio.create_subprocess_exec(
            python, str(entry),
            cwd=str(bot_dir),
            env=env,
            stdout=stdout_log,
            stderr=stderr_log,
            preexec_fn=os.setsid if os.name != "nt" else None,  # own process group
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
    """Stop a running bot gracefully, then kill any orphans."""
    bot = await _get_bot(bot_id)
    if not bot:
        return False, "Bot not found."

    killed_pids: list[int] = []

    # 1) Tracked handle
    proc = _processes.get(bot_id)
    if proc and proc.returncode is None:
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=8)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except Exception:
                    pass
            killed_pids.append(proc.pid)
        except Exception:
            pass

    # 2) DB-stored pid
    pid = bot.pid
    if pid and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            await asyncio.sleep(3)
            if _pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
            killed_pids.append(pid)
        except ProcessLookupError:
            pass
        except Exception:
            pass

    # 3) Orphan processes with same entry path
    try:
        if bot.entry_file:
            entry_path = str((_bot_dir(bot) / bot.entry_file).resolve())
            orphan_killed = _kill_orphans_by_entry(entry_path)
            killed_pids.extend(orphan_killed)
    except Exception:
        pass

    _processes.pop(bot_id, None)
    await _save(
        bot_id,
        pid=None,
        state=BotState.STOPPED,
        stopped_at=datetime.now(timezone.utc),
    )

    if killed_pids:
        return True, f"⏹ Bot stopped (killed PIDs: {killed_pids})"
    return True, "⏹ Bot stopped."


async def restart_bot(bot_id: int) -> tuple[bool, str]:
    await _save(bot_id, state=BotState.RESTARTING)
    await stop_bot(bot_id)
    # small grace period for Telegram to release the polling lock
    await asyncio.sleep(2)
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


async def kill_all_hosted_bots() -> None:
    """Graceful shutdown — called on manager SIGTERM/SIGINT."""
    try:
        async with AsyncSessionLocal() as s:
            result = await s.execute(select(Bot))
            bots = result.scalars().all()
        for bot in bots:
            try:
                await stop_bot(bot.id)
            except Exception:
                pass
    except Exception:
        pass
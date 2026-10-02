"""
services/logger.py — Bot log reading and management.
"""

from __future__ import annotations

from pathlib import Path

from config import LOG_TAIL_LINES


def _log_path(bot_dir: str, filename: str) -> Path:
    return Path(bot_dir) / "logs" / filename


def tail_log(bot_dir: str, filename: str = "stderr.log", lines: int = LOG_TAIL_LINES) -> str:
    path = _log_path(bot_dir, filename)
    if not path.exists():
        return "(no log yet)"
    try:
        content = path.read_text(errors="replace")
        all_lines = content.splitlines()
        return "\n".join(all_lines[-lines:]) or "(log is empty)"
    except Exception as e:
        return f"(error reading log: {e})"


def combined_tail(bot_dir: str, lines: int = LOG_TAIL_LINES) -> str:
    """Merge stdout + stderr into one view."""
    out = tail_log(bot_dir, "stdout.log", lines // 2)
    err = tail_log(bot_dir, "stderr.log", lines // 2)
    return f"📤 STDOUT:\n{out}\n\n📥 STDERR:\n{err}"


async def clear_bot_logs(bot_id: int | None = None, bot_dir: str | None = None) -> None:
    if bot_id and not bot_dir:
        from database import AsyncSessionLocal, Bot
        async with AsyncSessionLocal() as s:
            bot = await s.get(Bot, bot_id)
            if bot:
                bot_dir = bot.directory

    if not bot_dir:
        return

    log_dir = Path(bot_dir) / "logs"
    if log_dir.exists():
        for f in log_dir.iterdir():
            f.write_text("")


def log_file_path(bot_dir: str, filename: str = "stderr.log") -> Path:
    return _log_path(bot_dir, filename)

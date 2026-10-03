"""
services/watchdog_service.py — Watch bot files for changes and auto-restart.
Uses watchdog library for filesystem events.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from config import AUTO_RESTART_ON_CHANGE, BotState
from database import AsyncSessionLocal, Bot
from sqlalchemy import select

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

_observer: "Observer | None" = None
_loop: asyncio.AbstractEventLoop | None = None


class BotFileHandler(FileSystemEventHandler):
    def __init__(self, bot_id: int, bot_name: str):
        self.bot_id   = bot_id
        self.bot_name = bot_name
        self._last    = 0.0

    def on_modified(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        # Only watch .py files, ignore venv/logs/__pycache__
        if path.suffix != ".py":
            return
        if any(p in ("venv", "logs", "__pycache__") for p in path.parts):
            return

        import time
        now = time.time()
        if now - self._last < 5:   # debounce 5 seconds
            return
        self._last = now

        if _loop and AUTO_RESTART_ON_CHANGE:
            asyncio.run_coroutine_threadsafe(
                _auto_restart(self.bot_id, self.bot_name, path.name),
                _loop,
            )


async def _auto_restart(bot_id: int, bot_name: str, changed_file: str) -> None:
    from services.process_manager import restart_bot
    from services.notifier import notify

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if not bot or bot.state != BotState.RUNNING:
            return

    await notify(
        f"📁 <b>File Changed — Auto-Restart</b>\n\n"
        f"Bot: <b>{bot_name}</b>\n"
        f"File: <code>{changed_file}</code>",
        critical=False,
    )
    await restart_bot(bot_id)


def start_watching(bot_id: int, bot_dir: str, bot_name: str) -> None:
    global _observer
    if not WATCHDOG_AVAILABLE or not AUTO_RESTART_ON_CHANGE:
        return
    if _observer is None:
        _observer = Observer()
        _observer.start()

    handler = BotFileHandler(bot_id, bot_name)
    _observer.schedule(handler, bot_dir, recursive=True)


def stop_watching_all() -> None:
    global _observer
    if _observer:
        _observer.stop()
        _observer.join()
        _observer = None


async def init_watchdog(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop
    if not WATCHDOG_AVAILABLE or not AUTO_RESTART_ON_CHANGE:
        return

    async with AsyncSessionLocal() as s:
        result = await s.execute(select(Bot).where(Bot.state == BotState.RUNNING))
        bots   = result.scalars().all()

    for bot in bots:
        start_watching(bot.id, bot.directory, bot.name)

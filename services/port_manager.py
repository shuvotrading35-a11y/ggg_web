"""
services/port_manager.py — Detect ports used by hosted bot processes.
"""

from __future__ import annotations

import psutil

from database import AsyncSessionLocal, Bot
from sqlalchemy import select


async def get_bot_ports(bot_id: int) -> list[int]:
    """Return list of ports used by a bot's process."""
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if not bot or not bot.pid:
            return []
        pid = bot.pid

    try:
        proc = psutil.Process(pid)
        conns = proc.connections(kind="inet")
        return sorted({c.laddr.port for c in conns if c.laddr})
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


async def get_all_port_map() -> dict[str, list[int]]:
    """Return mapping of bot_name → [ports] for all running bots."""
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(Bot).where(Bot.pid != None))
        bots   = result.scalars().all()

    port_map: dict[str, list[int]] = {}
    for bot in bots:
        ports = await get_bot_ports(bot.id)
        if ports:
            port_map[bot.name] = ports

    return port_map


async def detect_port_conflicts() -> list[tuple[str, str, int]]:
    """Return list of (bot1_name, bot2_name, port) for shared ports."""
    port_map  = await get_all_port_map()
    port_users: dict[int, list[str]] = {}

    for bot_name, ports in port_map.items():
        for port in ports:
            port_users.setdefault(port, []).append(bot_name)

    conflicts = []
    for port, bots in port_users.items():
        if len(bots) > 1:
            for i in range(len(bots)):
                for j in range(i + 1, len(bots)):
                    conflicts.append((bots[i], bots[j], port))

    return conflicts

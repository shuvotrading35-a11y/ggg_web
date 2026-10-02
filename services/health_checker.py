"""
services/health_checker.py — Periodic health checks for hosted bots.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from config import HEALTH_CHECK_INTERVAL_SECONDS
from database import AsyncSessionLocal, Bot, HealthConfig
from sqlalchemy import select


async def _ping_telegram(token: str, timeout: int) -> tuple[bool, str]:
    """Call Telegram getMe to verify bot token is working."""
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url)
            data = r.json()
            if data.get("ok"):
                username = data["result"].get("username", "unknown")
                return True, f"@{username}"
            return False, data.get("description", "unknown error")
    except Exception as e:
        return False, str(e)


async def _ping_http(endpoint: str, timeout: int) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(endpoint)
            return r.status_code < 400, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


async def run_health_check(bot_id: int) -> tuple[bool, str]:
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if not bot or not bot.health_cfg:
            return False, "No health config."

        cfg: HealthConfig = bot.health_cfg
        check_type = cfg.check_type
        timeout    = cfg.timeout_seconds

    if check_type == "telegram_ping":
        # Read BOT_TOKEN from bot .env
        from pathlib import Path
        env_file = Path(bot.directory) / ".env"
        token = None
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("BOT_TOKEN="):
                    token = line.split("=", 1)[1].strip()
        if not token:
            return False, "BOT_TOKEN not found in bot .env"
        return await _ping_telegram(token, timeout)

    elif check_type == "http_ping" and cfg.endpoint:
        return await _ping_http(cfg.endpoint, timeout)

    return False, "Unknown check type."


async def health_check_loop() -> None:
    """Background loop — checks all health-check-enabled bots."""
    while True:
        await asyncio.sleep(HEALTH_CHECK_INTERVAL_SECONDS)
        try:
            async with AsyncSessionLocal() as s:
                result = await s.execute(
                    select(Bot).join(Bot.health_cfg).where(HealthConfig.enabled == True)
                )
                bots = result.scalars().all()

            for bot in bots:
                try:
                    ok, detail = await run_health_check(bot.id)
                    async with AsyncSessionLocal() as s:
                        cfg = await s.get(HealthConfig, bot.health_cfg.id)
                        if cfg:
                            cfg.last_check  = datetime.now(timezone.utc)
                            cfg.last_status = "healthy" if ok else "unhealthy"
                            await s.commit()

                    if not ok:
                        from services.notifier import notify
                        await notify(
                            f"❤️ <b>Health Check Failed</b>\n\n"
                            f"Bot: <b>{bot.name}</b>\nReason: {detail}",
                            critical=True,
                        )
                except Exception:
                    pass
        except Exception:
            pass

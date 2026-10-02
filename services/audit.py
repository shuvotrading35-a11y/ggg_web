"""
services/audit.py — Admin activity audit log.
"""

from __future__ import annotations

from database import AsyncSessionLocal, AuditLog
from sqlalchemy import select


async def log_action(action: str, detail: str | None = None, bot_name: str | None = None) -> None:
    async with AsyncSessionLocal() as s:
        s.add(AuditLog(action=action, detail=detail, bot_name=bot_name))
        await s.commit()


async def get_recent_logs(limit: int = 30) -> list[AuditLog]:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
        )
        return result.scalars().all()

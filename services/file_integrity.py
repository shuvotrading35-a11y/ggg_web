"""
services/file_integrity.py — SHA256 integrity checking for bot files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from database import AsyncSessionLocal, Bot, FileIntegrity
from sqlalchemy import select, delete


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


async def record_integrity(bot_id: int) -> int:
    """Hash all .py files and store. Returns count of files recorded."""
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if not bot:
            return 0
        bot_dir = Path(bot.directory)

        # Clear old records
        await s.execute(delete(FileIntegrity).where(FileIntegrity.bot_id == bot_id))

        count = 0
        for py_file in bot_dir.rglob("*.py"):
            if any(p in ("venv", "__pycache__") for p in py_file.parts):
                continue
            try:
                sha = _sha256(py_file)
                rel = str(py_file.relative_to(bot_dir))
                s.add(FileIntegrity(bot_id=bot_id, file_path=rel, sha256=sha))
                count += 1
            except Exception:
                pass

        await s.commit()
    return count


async def check_integrity(bot_id: int) -> list[dict]:
    """Compare current file hashes with stored ones. Returns list of changed files."""
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if not bot:
            return []
        bot_dir = Path(bot.directory)

        result = await s.execute(
            select(FileIntegrity).where(FileIntegrity.bot_id == bot_id)
        )
        records = {r.file_path: r.sha256 for r in result.scalars().all()}

    if not records:
        return []

    changed = []
    for rel_path, stored_hash in records.items():
        full_path = bot_dir / rel_path
        if not full_path.exists():
            changed.append({"file": rel_path, "status": "deleted"})
            continue
        current_hash = _sha256(full_path)
        if current_hash != stored_hash:
            changed.append({"file": rel_path, "status": "modified"})

    return changed

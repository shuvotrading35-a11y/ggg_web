"""
services/file_manager.py — Upload, extract, version management, import/export.
"""

from __future__ import annotations

import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from config import HOSTING_DIR, MAX_VERSIONS_KEPT
from database import AsyncSessionLocal, Bot, BotVersion
from utils.security import extract_zip_safe, safe_slug
from sqlalchemy import select


HOSTING_DIR.mkdir(parents=True, exist_ok=True)


def bot_directory(slug: str) -> Path:
    return HOSTING_DIR / slug


def detect_entry_points(bot_dir: Path) -> list[str]:
    """Return all .py files in root of bot dir (likely entry points)."""
    return sorted(p.name for p in bot_dir.glob("*.py"))


def detect_requirements(bot_dir: Path) -> bool:
    return (bot_dir / "requirements.txt").exists()


async def save_single_py(filename: str, content: bytes, slug: str) -> Path:
    """Save a single .py file as a new bot."""
    d = bot_directory(slug)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / Path(filename).name
    dest.write_bytes(content)
    return dest


async def extract_zip_bot(zip_path: Path, slug: str) -> tuple[Path, list[str]]:
    """Extract zip into bot directory, return (bot_dir, py_files)."""
    d = bot_directory(slug)
    d.mkdir(parents=True, exist_ok=True)
    extract_zip_safe(zip_path, d)
    py_files = detect_entry_points(d)
    return d, py_files


async def backup_bot(bot_id: int) -> Path | None:
    """Zip current bot files and store as a version. Returns backup path."""
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if not bot:
            return None
        bot_dir = Path(bot.directory)

        # Determine next version number
        result = await s.execute(
            select(BotVersion).where(BotVersion.bot_id == bot_id).order_by(BotVersion.version_num.desc())
        )
        latest = result.scalars().first()
        next_ver = (latest.version_num + 1) if latest else 1

        ver_dir = bot_dir / "versions"
        ver_dir.mkdir(exist_ok=True)
        ts  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        zip_path = ver_dir / f"v{next_ver}_{ts}.zip"

        # Zip everything except versions/ and venv/
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in bot_dir.rglob("*"):
                if any(part in ("versions", "venv", "logs", "__pycache__") for part in f.parts):
                    continue
                zf.write(f, f.relative_to(bot_dir))

        # Persist version record
        s.add(BotVersion(bot_id=bot_id, version_num=next_ver, backup_path=str(zip_path)))
        await s.commit()

        # Prune old versions
        result2 = await s.execute(
            select(BotVersion).where(BotVersion.bot_id == bot_id).order_by(BotVersion.version_num.desc())
        )
        all_versions = result2.scalars().all()
        for old in all_versions[MAX_VERSIONS_KEPT:]:
            Path(old.backup_path).unlink(missing_ok=True)
            await s.delete(old)
        await s.commit()

        return zip_path


async def rollback_bot(bot_id: int, version_id: int) -> tuple[bool, str]:
    """Restore a specific version. Bot must be stopped first."""
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        ver = await s.get(BotVersion, version_id)
        if not bot or not ver or ver.bot_id != bot_id:
            return False, "Version not found."

        bot_dir  = Path(bot.directory)
        zip_path = Path(ver.backup_path)
        if not zip_path.exists():
            return False, "Backup file missing."

        # Clear bot dir (except versions/ and venv/)
        for item in bot_dir.iterdir():
            if item.name in ("versions", "venv"):
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

        # Extract backup
        extract_zip_safe(zip_path, bot_dir)
        return True, f"✅ Rolled back to v{ver.version_num}."


async def export_bot_zip(bot_id: int) -> Path | None:
    """Create a full export .zip including .env (encrypted inline)."""
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if not bot:
            return None
        bot_dir = Path(bot.directory)
        export_path = bot_dir.parent / f"{bot.slug}_export.zip"

        with zipfile.ZipFile(export_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in bot_dir.rglob("*"):
                if any(part in ("venv", "__pycache__") for part in f.parts):
                    continue
                zf.write(f, f.relative_to(bot_dir.parent))

        return export_path


async def delete_bot_files(bot: Bot) -> None:
    bot_dir = Path(bot.directory)
    if bot_dir.exists():
        shutil.rmtree(bot_dir)

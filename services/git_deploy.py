"""
services/git_deploy.py — Git-based deploy: clone / pull / auto-deploy on push.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from config import GIT_DEFAULT_BRANCH
from database import AsyncSessionLocal, Bot, GitDeploy
from sqlalchemy import select


async def _run_git(args: list[str], cwd: str | None = None) -> tuple[bool, str]:
    cmd = ["git"] + args
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    output = stdout.decode(errors="replace") if stdout else ""
    return proc.returncode == 0, output


async def git_clone(bot_id: int, repo_url: str, branch: str = GIT_DEFAULT_BRANCH) -> tuple[bool, str]:
    """Clone a repo into the bot's directory."""
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if not bot:
            return False, "Bot not found."
        bot_dir = Path(bot.directory)

    # Clear existing files (keep venv, versions, logs)
    for item in bot_dir.iterdir():
        if item.name in ("venv", "versions", "logs", ".env"):
            continue
        import shutil
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    ok, out = await _run_git(
        ["clone", "--depth=1", "--branch", branch, repo_url, "."],
        cwd=str(bot_dir),
    )
    if not ok:
        return False, f"❌ Clone failed:\n<pre>{out[:800]}</pre>"

    # Get latest commit hash
    _, commit = await _run_git(["rev-parse", "--short", "HEAD"], cwd=str(bot_dir))
    commit = commit.strip()

    # Save git deploy record
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(GitDeploy).where(GitDeploy.bot_id == bot_id))
        gd = result.scalars().first()
        if gd:
            gd.repo_url    = repo_url
            gd.branch      = branch
            gd.last_commit = commit
            gd.last_deploy = datetime.now(timezone.utc)
        else:
            s.add(GitDeploy(
                bot_id=bot_id, repo_url=repo_url,
                branch=branch, last_commit=commit,
                last_deploy=datetime.now(timezone.utc),
            ))
        await s.commit()

    return True, f"✅ Cloned <code>{repo_url}</code>\nBranch: <code>{branch}</code>\nCommit: <code>{commit}</code>"


async def git_pull(bot_id: int) -> tuple[bool, str]:
    """Pull latest changes for a bot's linked repo."""
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        result = await s.execute(select(GitDeploy).where(GitDeploy.bot_id == bot_id))
        gd = result.scalars().first()
        if not bot or not gd:
            return False, "No Git repo linked to this bot."
        bot_dir = bot.directory

    ok, out = await _run_git(["pull", "origin", gd.branch], cwd=bot_dir)
    if not ok:
        return False, f"❌ Pull failed:\n<pre>{out[:800]}</pre>"

    _, commit = await _run_git(["rev-parse", "--short", "HEAD"], cwd=bot_dir)
    commit = commit.strip()

    async with AsyncSessionLocal() as s:
        result = await s.execute(select(GitDeploy).where(GitDeploy.bot_id == bot_id))
        gd = result.scalars().first()
        if gd:
            gd.last_commit = commit
            gd.last_deploy = datetime.now(timezone.utc)
            await s.commit()

    return True, f"✅ Pulled latest changes\nCommit: <code>{commit}</code>\n\n<pre>{out[:400]}</pre>"


async def detect_requirements_from_imports(bot_dir: str) -> list[str]:
    """
    Scan .py files for import statements and return likely pip packages.
    This is a best-effort detection — not 100% accurate.
    """
    import ast, re

    stdlib = {
        "os", "sys", "re", "json", "time", "datetime", "pathlib", "math",
        "random", "string", "io", "abc", "typing", "collections", "itertools",
        "functools", "threading", "asyncio", "subprocess", "logging", "hashlib",
        "base64", "copy", "uuid", "enum", "dataclasses", "contextlib",
        "traceback", "inspect", "shutil", "tempfile", "glob", "signal",
        "socket", "struct", "unittest", "warnings", "weakref", "zipfile",
    }

    # Known import name → pip package name
    pip_map = {
        "telegram":      "python-telegram-bot",
        "telebot":       "pyTelegramBotAPI",
        "aiogram":       "aiogram",
        "pyrogram":      "pyrogram",
        "dotenv":        "python-dotenv",
        "sqlalchemy":    "SQLAlchemy",
        "aiosqlite":     "aiosqlite",
        "psutil":        "psutil",
        "requests":      "requests",
        "httpx":         "httpx",
        "aiohttp":       "aiohttp",
        "fastapi":       "fastapi",
        "uvicorn":       "uvicorn",
        "pydantic":      "pydantic",
        "redis":         "redis",
        "pymongo":       "pymongo",
        "PIL":           "Pillow",
        "cv2":           "opencv-python",
        "numpy":         "numpy",
        "pandas":        "pandas",
        "cryptography":  "cryptography",
        "bs4":           "beautifulsoup4",
        "lxml":          "lxml",
        "yaml":          "PyYAML",
        "toml":          "toml",
        "apscheduler":   "APScheduler",
        "celery":        "celery",
        "motor":         "motor",
        "tortoise":      "tortoise-orm",
        "peewee":        "peewee",
        "alembic":       "alembic",
    }

    found: set[str] = set()

    for py_file in Path(bot_dir).rglob("*.py"):
        if any(p in ("venv", "__pycache__") for p in py_file.parts):
            continue
        try:
            tree = ast.parse(py_file.read_text(errors="replace"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = (
                        [alias.name for alias in node.names]
                        if isinstance(node, ast.Import)
                        else ([node.module] if node.module else [])
                    )
                    for name in names:
                        base = name.split(".")[0]
                        if base not in stdlib and base:
                            pkg = pip_map.get(base, base)
                            found.add(pkg)
        except Exception:
            pass

    return sorted(found)


async def write_auto_requirements(bot_id: int) -> tuple[bool, str]:
    """Auto-generate requirements.txt from import scanning."""
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        if not bot:
            return False, "Bot not found."
        bot_dir = bot.directory

    packages = await detect_requirements_from_imports(bot_dir)
    if not packages:
        return False, "No external packages detected."

    req_path = Path(bot_dir) / "requirements.txt"
    req_path.write_text("\n".join(packages) + "\n")

    return True, f"✅ Generated <code>requirements.txt</code>\n\n" + "\n".join(f"• {p}" for p in packages)

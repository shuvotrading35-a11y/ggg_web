"""
services/git_deploy.py — Git deploy via HTTP archive (no git binary needed).

Functions:
  • git_clone(bot_id, repo_url, branch)     → download + extract + install
  • git_pull(bot_id)                        → same but preserve .env/logs/data
  • write_auto_requirements(bot_id)         → scan imports → requirements.txt
"""

from __future__ import annotations

import io
import logging
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from database import AsyncSessionLocal, Bot, GitDeploy

logger = logging.getLogger(__name__)


# Files/folders that must NOT be overwritten by a pull
PRESERVE = {
    ".env", "logs", "data", "venv", ".venv",
    "__pycache__", "bot.log", "stdout.log", "stderr.log",
    "backup", "backups", "requirements.txt",
}


# ═══════════════════════════════════════════════════════════════════════════
# URL parsing
# ═══════════════════════════════════════════════════════════════════════════

def _parse_github(url: str) -> tuple[str, str] | None:
    """Return (owner, repo) or None."""
    try:
        u = (url or "").strip()
        if u.startswith("git@github.com:"):
            u = "https://github.com/" + u.split(":", 1)[1]
        p = urlparse(u)
        if p.netloc not in ("github.com", "www.github.com"):
            return None
        parts = [x for x in p.path.strip("/").split("/") if x]
        if len(parts) < 2:
            return None
        owner, repo = parts[0], parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        return owner, repo
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# GitHub API helpers
# ═══════════════════════════════════════════════════════════════════════════

async def get_latest_sha(owner: str, repo: str, branch: str = "main") -> str | None:
    url = f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(url, headers={"Accept": "application/vnd.github+json"})
            if r.status_code != 200:
                return None
            return r.json().get("sha")
    except Exception:
        return None


async def _download_archive(owner: str, repo: str, branch: str) -> bytes | None:
    """Download GitHub auto-generated ZIP of the branch."""
    url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"
    try:
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code != 200:
                return None
            return r.content
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Extract archive (flatten top-level folder)
# ═══════════════════════════════════════════════════════════════════════════

def _extract_to(zip_bytes: bytes, dest: Path) -> bool:
    """Extract GitHub archive into dest, flattening the single top folder."""
    tmp = dest.parent / (dest.name + "_extract_tmp")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(tmp)
    except Exception as e:
        logger.exception(f"extract failed: {e}")
        shutil.rmtree(tmp, ignore_errors=True)
        return False

    # GitHub archive has a single top folder: <repo>-<branch>/
    children = [p for p in tmp.iterdir() if p.is_dir()]
    src_root = children[0] if len(children) == 1 else tmp

    dest.mkdir(parents=True, exist_ok=True)
    for item in src_root.iterdir():
        target = dest / item.name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink()
        shutil.move(str(item), str(target))

    shutil.rmtree(tmp, ignore_errors=True)
    return True


# ═══════════════════════════════════════════════════════════════════════════
# git_clone — initial download into empty bot directory
# ═══════════════════════════════════════════════════════════════════════════

async def git_clone(bot_id: int, repo_url: str, branch: str = "main") -> tuple[bool, str]:
    parsed = _parse_github(repo_url)
    if not parsed:
        return False, "❌ Only GitHub URLs are supported right now."
    owner, repo = parsed
    branch = (branch or "main").strip() or "main"

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        return False, "Bot not found."

    bot_dir = Path(bot.directory)
    bot_dir.mkdir(parents=True, exist_ok=True)

    # 1. Download
    data = await _download_archive(owner, repo, branch)
    if not data:
        return False, (
            f"❌ Download failed for <code>{owner}/{repo}@{branch}</code>.\n"
            f"Repo public? Branch name correct?"
        )

    # 2. Extract with preserve of existing protected paths
    preserved = _snapshot_preserved(bot_dir)

    # Wipe bot dir but keep preserved
    _wipe_dir(bot_dir)
    bot_dir.mkdir(parents=True, exist_ok=True)

    if not _extract_to(data, bot_dir):
        _restore_preserved(bot_dir, preserved)
        return False, "❌ Extract failed."

    _restore_preserved(bot_dir, preserved)

    # 3. Record in GitDeploy
    sha = await get_latest_sha(owner, repo, branch)
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as s:
        result = await s.execute(select(GitDeploy).where(GitDeploy.bot_id == bot_id))
        gd = result.scalars().first()
        if not gd:
            gd = GitDeploy(bot_id=bot_id)
            s.add(gd)
        gd.repo_url = repo_url
        gd.branch = branch
        gd.last_commit = sha
        gd.last_deploy = now
        gd.last_check_at = now
        await s.commit()

    short = (sha or "—")[:8]
    return True, f"✅ Cloned <code>{owner}/{repo}@{branch}</code>\nCommit: <code>{short}</code>"


# ═══════════════════════════════════════════════════════════════════════════
# git_pull — update existing bot, preserve .env/logs/data
# ═══════════════════════════════════════════════════════════════════════════

async def git_pull(bot_id: int) -> tuple[bool, str]:
    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
        result = await s.execute(select(GitDeploy).where(GitDeploy.bot_id == bot_id))
        gd = result.scalars().first()

    if not bot:
        return False, "Bot not found."
    if not gd:
        return False, "No repo linked. Use 🔗 Link Git Repo first."

    parsed = _parse_github(gd.repo_url)
    if not parsed:
        return False, "Repo URL is not a valid GitHub URL."
    owner, repo = parsed
    branch = gd.branch or "main"

    # 1. Check SHA (optional — for the message)
    sha = await get_latest_sha(owner, repo, branch)
    if sha and gd.last_commit and sha == gd.last_commit:
        return False, f"ℹ️ Already up to date (<code>{sha[:8]}</code>)."

    # 2. Download
    data = await _download_archive(owner, repo, branch)
    if not data:
        return False, f"❌ Download failed for <code>{owner}/{repo}@{branch}</code>."

    # 3. Preserve protected paths
    bot_dir = Path(bot.directory)
    preserved = _snapshot_preserved(bot_dir)

    # 4. Wipe + extract + restore
    _wipe_dir(bot_dir)
    bot_dir.mkdir(parents=True, exist_ok=True)

    if not _extract_to(data, bot_dir):
        _restore_preserved(bot_dir, preserved)
        return False, "❌ Extract failed."

    _restore_preserved(bot_dir, preserved)

    # 5. Update SHA
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as s:
        g = await s.get(GitDeploy, gd.id)
        if g:
            g.last_commit = sha
            g.last_deploy = now
            g.last_check_at = now
            await s.commit()

    short = (sha or "—")[:8]
    return True, f"✅ Pulled <code>{owner}/{repo}@{branch}</code>\nCommit: <code>{short}</code>"


# ═══════════════════════════════════════════════════════════════════════════
# Auto-requirements — scan imports and write requirements.txt
# ═══════════════════════════════════════════════════════════════════════════

async def write_auto_requirements(bot_id: int) -> tuple[bool, str]:
    """Scan .py files for import statements and write requirements.txt."""
    import ast

    async with AsyncSessionLocal() as s:
        bot = await s.get(Bot, bot_id)
    if not bot:
        return False, "Bot not found."

    bot_dir = Path(bot.directory)
    stdlib = set(getattr(__import__("sys"), "stdlib_module_names", set()))

    found: set[str] = set()
    for py in bot_dir.rglob("*.py"):
        if any(p in py.parts for p in ("venv", ".venv", "__pycache__", ".git")):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    found.add(n.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    found.add(node.module.split(".")[0])

    # Keep only third-party-looking imports
    third_party = sorted(
        m for m in found
        if m and m not in stdlib and not m.startswith("_")
        and m not in {"utils", "handlers", "api", "services", "keyboards",
                      "config", "database", "bot"}
    )

    if not third_party:
        return False, "⚠️ No third-party imports detected."

    # Map common import names → pip package names
    NAME_MAP = {
        "telegram": "python-telegram-bot>=20",
        "dotenv":   "python-dotenv",
        "PIL":      "Pillow",
        "cv2":      "opencv-python",
        "yaml":     "PyYAML",
        "bs4":      "beautifulsoup4",
    }
    pkgs = sorted({NAME_MAP.get(m, m) for m in third_party})

    req = bot_dir / "requirements.txt"
    req.write_text("\n".join(pkgs) + "\n", encoding="utf-8")

    return True, "📦 Wrote requirements.txt:\n<pre>" + "\n".join(pkgs) + "</pre>"


# ═══════════════════════════════════════════════════════════════════════════
# Preserve / wipe helpers
# ═══════════════════════════════════════════════════════════════════════════

def _snapshot_preserved(bot_dir: Path) -> dict[str, Path]:
    """Copy protected paths to a temp dir; return {name: temp_path}."""
    if not bot_dir.exists():
        return {}
    tmp_root = Path(tempfile.mkdtemp(prefix=f".{bot_dir.name}_pres_"))
    out: dict[str, Path] = {}
    for name in PRESERVE:
        src = bot_dir / name
        if not src.exists():
            continue
        dst = tmp_root / name
        try:
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
            out[name] = dst
        except Exception as e:
            logger.warning(f"preserve {name}: {e}")
    return out


def _restore_preserved(bot_dir: Path, preserved: dict[str, Path]) -> None:
    for name, src in preserved.items():
        if not src.exists():
            continue
        target = bot_dir / name
        try:
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink()
            if src.is_dir():
                shutil.copytree(src, target, dirs_exist_ok=True)
            else:
                shutil.copy2(src, target)
        except Exception as e:
            logger.warning(f"restore {name}: {e}")
        finally:
            try:
                if src.parent.exists():
                    shutil.rmtree(src.parent, ignore_errors=True)
            except Exception:
                pass


def _wipe_dir(bot_dir: Path) -> None:
    """Remove all contents of bot_dir (but keep the dir itself)."""
    if not bot_dir.exists():
        return
    for item in bot_dir.iterdir():
        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink()
        except Exception:
            pass
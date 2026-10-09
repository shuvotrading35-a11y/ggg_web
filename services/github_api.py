"""
services/github_api.py — GitHub API helpers.

- parse_github_url(url) → (owner, repo) or None
- get_latest_sha(owner, repo, branch, token=None) → SHA or None
- download_archive(owner, repo, branch) → bytes or None
"""

from __future__ import annotations

import io
import zipfile
from urllib.parse import urlparse

import httpx


GITHUB_API = "https://api.github.com"
CODELOAD = "https://codeload.github.com"


def parse_github_url(url: str) -> tuple[str, str] | None:
    """Return (owner, repo) or None."""
    try:
        p = urlparse((url or "").strip())
        if p.scheme != "https" or p.netloc not in ("github.com", "www.github.com"):
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


async def get_latest_sha(
    owner: str, repo: str,
    branch: str = "main",
    token: str | None = None,
) -> str | None:
    """Return the latest commit SHA on branch, or None on error."""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits/{branch}"

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(url, headers=headers)
            if r.status_code != 200:
                return None
            return r.json().get("sha")
    except Exception:
        return None


async def download_archive(
    owner: str, repo: str, branch: str = "main",
) -> bytes | None:
    """Download GitHub's auto-generated zip archive of the branch."""
    url = f"{CODELOAD}/{owner}/{repo}/zip/refs/heads/{branch}"
    try:
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code != 200:
                return None
            return r.content
    except Exception:
        return None


def extract_archive(zip_bytes: bytes, dest_dir) -> bool:
    """
    Extract GitHub archive (top-level folder <repo>-<branch>/)
    and flatten contents into dest_dir.
    """
    from pathlib import Path
    import shutil

    dest = Path(dest_dir)
    tmp = dest.parent / (dest.name + "_extract_tmp")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(tmp)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        return False

    # GitHub archive has one top folder: <repo>-<branch>/
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
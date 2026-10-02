"""
utils/security.py — Encryption, path-traversal protection, filename sanitisation.
"""

import re
import unicodedata
import zipfile
from pathlib import Path

from cryptography.fernet import Fernet

from config import MASTER_KEY


# ── Fernet cipher ─────────────────────────────────────────────────────────────

_cipher = Fernet(MASTER_KEY.encode())


def encrypt(value: str) -> str:
    return _cipher.encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _cipher.decrypt(token.encode()).decode()


def mask(value: str, show: int = 4) -> str:
    """Return last `show` chars with leading asterisks."""
    if len(value) <= show:
        return "*" * len(value)
    return "*" * (len(value) - show) + value[-show:]


# ── Filename / path sanitisation ──────────────────────────────────────────────

_SAFE_SLUG = re.compile(r"[^\w\-]")


def safe_slug(name: str) -> str:
    """Convert arbitrary string to a filesystem-safe slug."""
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode()
    name = _SAFE_SLUG.sub("_", name).strip("_").lower()
    return name or "bot"


def safe_filename(filename: str) -> str:
    """Strip directory components and dangerous characters."""
    name = Path(filename).name
    return safe_slug(Path(name).stem) + Path(name).suffix.lower()


# ── ZIP security ──────────────────────────────────────────────────────────────

class ZipSlipError(Exception):
    pass


def validate_zip(zip_path: Path, target_dir: Path) -> list[str]:
    """
    Open zip and verify no member tries to escape target_dir (ZIP Slip).
    Returns list of safe member names.
    Raises ZipSlipError on path traversal attempt.
    """
    target_dir = target_dir.resolve()
    safe_members: list[str] = []

    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            member_path = (target_dir / member.filename).resolve()
            if not str(member_path).startswith(str(target_dir)):
                raise ZipSlipError(f"Path traversal detected: {member.filename}")
            safe_members.append(member.filename)

    return safe_members


def extract_zip_safe(zip_path: Path, target_dir: Path) -> list[str]:
    """Extract zip after validation. Returns extracted file list."""
    members = validate_zip(zip_path, target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(target_dir)

    return members


# ── Admin check ───────────────────────────────────────────────────────────────

from config import ADMIN_IDS


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

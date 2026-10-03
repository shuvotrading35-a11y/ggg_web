"""
services/terminal.py — Safe whitelisted terminal for Admin via Telegram.
Only allows commands from TERMINAL_ALLOWED list.
"""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path

from config import TERMINAL_ALLOWED, HOSTING_DIR


class TerminalDenied(Exception):
    pass


def _extract_base_cmd(command: str) -> str:
    """Extract the base command name from a shell command string."""
    try:
        parts = shlex.split(command)
        return Path(parts[0]).name if parts else ""
    except ValueError:
        return ""


def is_allowed(command: str) -> bool:
    base = _extract_base_cmd(command)
    return base in TERMINAL_ALLOWED


async def run_command(command: str, cwd: str | None = None) -> tuple[bool, str]:
    """
    Run a whitelisted shell command.
    Returns (success, output).
    """
    if not is_allowed(command):
        base = _extract_base_cmd(command)
        raise TerminalDenied(
            f"❌ Command <code>{base}</code> is not allowed.\n\n"
            f"Allowed: <code>{', '.join(sorted(TERMINAL_ALLOWED))}</code>"
        )

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd or str(HOSTING_DIR),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode(errors="replace") if stdout else ""
        return proc.returncode == 0, output or "(no output)"
    except asyncio.TimeoutError:
        return False, "⏱ Command timed out (30s limit)."
    except Exception as e:
        return False, f"Error: {e}"

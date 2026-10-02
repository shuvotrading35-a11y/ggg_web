import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
BOT_TOKEN: str = os.environ["BOT_TOKEN"]
ADMIN_IDS: list[int] = [
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
]

# ── Encryption ────────────────────────────────────────────────────────────────
MASTER_KEY: str = os.environ["MASTER_KEY"]

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
HOSTING_DIR = Path(os.getenv("HOSTING_DIR", "/opt/shuvo-hosting/storage/bots"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/opt/shuvo-hosting/data"))
DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{DATA_DIR}/hosting.db")

# ── Limits ────────────────────────────────────────────────────────────────────
MAX_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))
MAX_ZIP_SIZE_MB: int = int(os.getenv("MAX_ZIP_SIZE_MB", "100"))
MAX_UPLOAD_BYTES: int = MAX_UPLOAD_SIZE_MB * 1024 * 1024
MAX_ZIP_BYTES: int = MAX_ZIP_SIZE_MB * 1024 * 1024

# ── Auto-restart ──────────────────────────────────────────────────────────────
MAX_RESTART_ATTEMPTS: int = int(os.getenv("MAX_RESTART_ATTEMPTS", "5"))
RESTART_WINDOW_MINUTES: int = int(os.getenv("RESTART_WINDOW_MINUTES", "10"))

# ── Logs ──────────────────────────────────────────────────────────────────────
LOG_TAIL_LINES: int = int(os.getenv("LOG_TAIL_LINES", "50"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ── History ───────────────────────────────────────────────────────────────────
PERF_HISTORY_DAYS: int = int(os.getenv("PERF_HISTORY_DAYS", "30"))
MAX_VERSIONS_KEPT: int = 5

# ── Webhook ───────────────────────────────────────────────────────────────────
WEBHOOK_BASE_URL: str = os.getenv("WEBHOOK_BASE_URL", "")

# ── Quiet hours (notifications suppressed except critical) ────────────────────
QUIET_HOURS_START: int = 2   # 2 AM
QUIET_HOURS_END: int = 7     # 7 AM

# ── Bot states ────────────────────────────────────────────────────────────────
class BotState:
    RUNNING     = "running"
    STOPPED     = "stopped"
    STARTING    = "starting"
    RESTARTING  = "restarting"
    INSTALLING  = "installing"
    CRASHED     = "crashed"
    MAINTENANCE = "maintenance"

STATE_EMOJI = {
    BotState.RUNNING:     "🟢",
    BotState.STOPPED:     "🔴",
    BotState.STARTING:    "🟡",
    BotState.RESTARTING:  "🟠",
    BotState.INSTALLING:  "🔵",
    BotState.CRASHED:     "⚠️",
    BotState.MAINTENANCE: "🔧",
}

# ── Allowed file extensions ───────────────────────────────────────────────────
ALLOWED_EXTENSIONS = {".py", ".zip"}

# ── Health check intervals ────────────────────────────────────────────────────
HEALTH_CHECK_INTERVAL_SECONDS: int = 300   # 5 min
PERF_SAMPLE_INTERVAL_SECONDS: int  = 60    # 1 min

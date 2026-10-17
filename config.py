"""Central configuration, read from environment variables / a .env file."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        sys.exit(f"Missing required setting {name}. Copy .env.example to .env and fill it in.")
    return value


# --- Telegram -------------------------------------------------------------
BOT_TOKEN = _required("BOT_TOKEN")

# Fail closed: this bot compiles and runs uploaded code on your VPS, so only
# the Telegram user IDs listed here are ever allowed to use it.
ALLOWED_USER_IDS = {
    int(x) for x in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x
}
if not ALLOWED_USER_IDS:
    sys.exit("ALLOWED_USER_IDS is empty. Add your Telegram user ID to .env (comma separated).")

# --- MetaTrader (dedicated PORTABLE install) -------------------------------
# Folder that contains terminal64.exe and MetaEditor64.exe.
# In portable mode (/portable flag) this folder is also the data folder.
TERMINAL_DIR = Path(_required("TERMINAL_DIR"))
TERMINAL_EXE = TERMINAL_DIR / "terminal64.exe"
METAEDITOR_EXE = TERMINAL_DIR / "MetaEditor64.exe"

MQL5_DIR = TERMINAL_DIR / "MQL5"
EXPERTS_DIR = MQL5_DIR / "Experts" / "BacktestBot"      # uploaded EAs land here
EXPERT_REL_PREFIX = "BacktestBot"                        # relative to MQL5\Experts
PROFILES_TESTER = MQL5_DIR / "Profiles" / "Tester"       # .set files for the tester
REPORTS_DIR = TERMINAL_DIR / "reports"                   # tester reports land here
REPORT_REL_PREFIX = "reports"                            # relative to TERMINAL_DIR

# --- Local working files ---------------------------------------------------
WORK_DIR = Path(os.getenv("WORK_DIR", "work")).resolve()  # ini files, compile logs

# --- Timeouts --------------------------------------------------------------
COMPILE_TIMEOUT_S = int(os.getenv("COMPILE_TIMEOUT_S", "180"))
TEST_TIMEOUT_S = int(os.getenv("TEST_TIMEOUT_MIN", "360")) * 60

# --- Default tester settings (can be overridden per upload) ---------------
# Model: 0 every tick, 1 1-minute OHLC, 2 open prices only, 3 math calc,
#        4 every tick based on real ticks
TESTER_DEFAULTS = {
    "symbol": os.getenv("DEFAULT_SYMBOL", "XAUUSD"),
    "period": os.getenv("DEFAULT_PERIOD", "H1"),
    "model": os.getenv("DEFAULT_MODEL", "1"),
    "from_date": os.getenv("DEFAULT_FROM", "2025.01.01"),
    "to_date": os.getenv("DEFAULT_TO", "2025.10.01"),
    "deposit": os.getenv("DEFAULT_DEPOSIT", "10000"),
    "currency": os.getenv("DEFAULT_CURRENCY", "USD"),
    "leverage": os.getenv("DEFAULT_LEVERAGE", "100"),
}

for _d in (WORK_DIR, EXPERTS_DIR, PROFILES_TESTER, REPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

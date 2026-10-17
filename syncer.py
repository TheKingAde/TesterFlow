"""Copy custom includes/libraries from the everyday terminal into the backtest one.

The source MQL5 folder comes from, in order:
  1. /setsource in Telegram (remembered in work\\source_mql5.txt; 'off' disables it)
  2. SYNC_SOURCE_MQL5 in .env
"""
import shutil
from pathlib import Path
from typing import Iterable, Optional, Tuple

import config

_STATE_FILE = config.WORK_DIR / "source_mql5.txt"
LAST_SYNC = "never"


def _normalise(raw: str) -> Optional[Path]:
    """Accept the MQL5 folder itself, or the terminal data folder that holds it."""
    path = Path(raw.strip().strip('"').strip("'"))
    if (path / "MQL5").is_dir():
        path = path / "MQL5"
    if path.is_dir() and path.name.lower() == "mql5":
        return path
    return None


def get_source() -> Optional[Path]:
    """Current source MQL5 folder, or None if syncing is off / not set."""
    if _STATE_FILE.exists():
        text = _STATE_FILE.read_text(encoding="utf-8").strip()
        if text.lower() == "off":
            return None
        found = _normalise(text)
        if found:
            return found
    return config.SYNC_SOURCE_MQL5


def set_source(raw: str) -> Tuple[bool, str]:
    """Persist a new source path from Telegram. 'off' disables syncing."""
    if raw.strip().lower() in ("off", "none", "disable"):
        _STATE_FILE.write_text("off", encoding="utf-8")
        return True, "Include sync is now off."
    found = _normalise(raw)
    if found is None:
        return False, (
            "That is not an MQL5 folder. Use the path ending in \\MQL5, or the "
            "terminal data folder that contains it (MT5: File > Open Data Folder)."
        )
    _STATE_FILE.write_text(str(found), encoding="utf-8")
    return True, f"Source set to {found}"


def _copy_newer(src: Path, dst: Path, patterns: Iterable[str] = ("*",)) -> int:
    """Copy files from src to dst (same relative paths) if missing or changed."""
    if not src.is_dir():
        return 0
    copied = 0
    for file in src.rglob("*"):
        if not file.is_file():
            continue
        if not any(file.match(p) for p in patterns):
            continue
        target = dst / file.relative_to(src)
        if target.exists():
            s, t = file.stat(), target.stat()
            if t.st_size == s.st_size and t.st_mtime >= s.st_mtime:
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)
        copied += 1
    return copied


def sync_sources() -> int:
    """Blocking. Returns the number of files copied (0 if sync is not configured)."""
    global LAST_SYNC
    src = get_source()
    if src is None:
        LAST_SYNC = "no source path set"
        return 0
    if not src.is_dir():
        raise FileNotFoundError(f"Include source folder not found: {src}")
    if src.resolve() == config.MQL5_DIR.resolve():
        LAST_SYNC = "source is the backtest terminal itself, nothing to do"
        return 0

    copied = 0
    for folder in ("Include", "Libraries", "Indicators"):
        copied += _copy_newer(src / folder, config.MQL5_DIR / folder)
    if config.SYNC_EXPERTS_SUBDIR:
        copied += _copy_newer(
            src / "Experts" / config.SYNC_EXPERTS_SUBDIR,
            config.EXPERTS_DIR,
            patterns=("*.mqh",),
        )
    LAST_SYNC = f"{copied} file(s) copied from {src}"
    return copied

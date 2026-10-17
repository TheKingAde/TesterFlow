"""Launch the MT5 Strategy Tester from the command line and collect outputs."""
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import config
from mt5files import read_text_any


class TesterRunner:
    """Runs one terminal process at a time and lets /cancel kill it."""

    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None

    def run(self, ini_path: Path, timeout_s: int) -> Optional[int]:
        """Blocking. Returns the exit code. Raises subprocess.TimeoutExpired."""
        cmd = f'"{config.TERMINAL_EXE}" /portable /config:"{ini_path}"'
        self.proc = subprocess.Popen(cmd)
        try:
            return self.proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self.cancel()
            raise
        finally:
            self.proc = None

    def cancel(self) -> bool:
        proc = self.proc
        if proc and proc.poll() is None:
            proc.kill()
            return True
        return False


@dataclass
class Outputs:
    html: Optional[Path]
    png: Optional[Path]


def collect_outputs(stem: str) -> Outputs:
    """Find the report (.htm/.html) and the balance/equity chart (.png) for a run."""
    html = png = None
    for folder in (config.REPORTS_DIR, config.TERMINAL_DIR):
        if html is None:
            for ext in (".htm", ".html"):
                candidate = folder / f"{stem}{ext}"
                if candidate.exists():
                    html = candidate
                    break
        if png is None:
            matches = sorted(folder.glob(f"{stem}*.png"))
            if matches:
                png = matches[0]
    return Outputs(html=html, png=png)


def _tail_of_newest(files: list, lines: int) -> str:
    if not files:
        return "(none found)"
    newest = max(files, key=lambda p: p.stat().st_mtime)
    text = read_text_any(newest).strip().splitlines()
    body = "\n".join(text[-lines:]) or "(empty)"
    return f"{newest.name}:\n{body}"


def tester_log_tail(lines: int = 12) -> str:
    """Last lines of the newest tester log AND terminal journal, for failure messages.

    The terminal journal (logs\\) records login/authorization, which the tester
    log does not, so both are needed to diagnose "account is not specified".
    """
    tester_files = list((config.TERMINAL_DIR / "Tester" / "logs").glob("*.log"))
    tester_files += list((config.TERMINAL_DIR / "Tester").glob("Agent-*/logs/*.log"))
    terminal_files = list((config.TERMINAL_DIR / "logs").glob("*.log"))
    return (
        "TESTER LOG " + _tail_of_newest(tester_files, lines)
        + "\n\nTERMINAL JOURNAL " + _tail_of_newest(terminal_files, lines)
    )


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------
_LINE_TIME = re.compile(r"^\S+\s+\d+\s+(\d\d):(\d\d):(\d\d)")


def terminal_running() -> bool:
    """True if any terminal64.exe process is currently running."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq terminal64.exe", "/NH"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        return False
    return "terminal64.exe" in out.lower()


def journal_tail(lines: int = 30) -> str:
    """Last lines of the newest terminal journal."""
    files = list((config.TERMINAL_DIR / "logs").glob("*.log"))
    return _tail_of_newest(files, lines)


def _journal_lines_since(started: float) -> List[str]:
    """Terminal journal lines written since `started` (a time.time() value)."""
    files = list((config.TERMINAL_DIR / "logs").glob("*.log"))
    if not files:
        return []
    newest = max(files, key=lambda p: p.stat().st_mtime)
    lt = time.localtime(started - 5)
    floor = lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec
    picked = []
    try:
        text = read_text_any(newest)
    except OSError:
        return []
    for ln in text.splitlines():
        match = _LINE_TIME.match(ln)
        if match:
            secs = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3))
            if secs >= floor:
                picked.append(ln)
    return picked


def journal_since(started: float, max_lines: int = 40) -> str:
    """Journal lines from one run, as text for a Telegram message."""
    lines = _journal_lines_since(started)
    if not lines:
        return "(no terminal journal lines since this run started)"
    return "\n".join(lines[-max_lines:])


def saved_account_summary() -> str:
    """Login/Server saved in the backtest terminal's config\\common.ini (no password)."""
    ini = config.TERMINAL_DIR / "config" / "common.ini"
    try:
        text = read_text_any(ini)
    except OSError:
        return "common.ini not found"
    found = []
    for key in ("Login", "Server"):
        match = re.search(rf"(?im)^\s*{key}\s*=\s*(.*)$", text)
        found.append(f"{key}={match.group(1).strip() if match else '(missing)'}")
    return ", ".join(found)


# --------------------------------------------------------------------------
# Login warm-up
# --------------------------------------------------------------------------
_AUTH_OK = re.compile(r"authorized on", re.IGNORECASE)
_AUTH_FAIL = re.compile(r"authorization.*failed|invalid account", re.IGNORECASE)


def _close_gracefully(proc: subprocess.Popen) -> None:
    """Ask the terminal to close by PID so it saves its account, then force if needed.

    Done by PID on purpose: closing by image name would also close your everyday
    terminal if it is running.
    """
    if proc.poll() is not None:
        return
    subprocess.run(["taskkill", "/PID", str(proc.pid)], capture_output=True)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


def warm_up(timeout_s: int = 240) -> Tuple[bool, str]:
    """Blocking. Start the backtest terminal, wait until it is logged in, close it.

    Uses MT5_LOGIN / MT5_SERVER / MT5_PASSWORD from .env when set, otherwise the
    account already saved in the terminal. Returns (ok, message for Telegram).
    """
    started = time.time()
    ini = config.WORK_DIR / "login_warmup.ini"
    cmd = f'"{config.TERMINAL_EXE}" /portable'
    used_env = bool(config.MT5_LOGIN and config.MT5_SERVER)
    if used_env:
        lines = ["[Common]", f"Login={config.MT5_LOGIN}"]
        if config.MT5_PASSWORD:
            lines.append(f"Password={config.MT5_PASSWORD}")
        lines.append(f"Server={config.MT5_SERVER}")
        ini.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        cmd += f' /config:"{ini}"'

    proc = subprocess.Popen(cmd)
    ok = False
    detail = ""
    try:
        deadline = started + timeout_s
        while time.time() < deadline:
            if proc.poll() is not None:
                detail = "the terminal exited before it connected"
                break
            seen = _journal_lines_since(started)
            if any(_AUTH_OK.search(ln) for ln in seen):
                ok = True
                time.sleep(15)  # let it finish syncing symbols before closing
                break
            if any(_AUTH_FAIL.search(ln) for ln in seen):
                detail = "the broker rejected the login (check login, password, server)"
                break
            time.sleep(3)
        else:
            detail = f"no 'authorized on' line within {timeout_s}s"
    finally:
        _close_gracefully(proc)
        ini.unlink(missing_ok=True)  # it holds the account password

    source = ".env account" if used_env else "account saved in the terminal"
    if ok:
        return True, (
            f"✅ Backtest terminal connected using the {source}.\n"
            f"Saved in terminal: {saved_account_summary()}"
        )
    return False, (
        f"❌ Could not log in using the {source}: {detail}.\n"
        f"Saved in terminal: {saved_account_summary()}\n\n"
        "Terminal journal during this attempt:\n" + journal_since(started)
    )
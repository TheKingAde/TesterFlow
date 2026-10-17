"""Launch the MT5 Strategy Tester from the command line and collect outputs."""
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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


def tester_log_tail(lines: int = 15) -> str:
    """Last lines of the newest tester log, for failure messages."""
    candidates = list((config.TERMINAL_DIR / "Tester" / "logs").glob("*.log"))
    candidates += list((config.TERMINAL_DIR / "Tester").glob("Agent-*/logs/*.log"))
    if not candidates:
        return "(no tester log found)"
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    text = read_text_any(newest).strip().splitlines()
    return "\n".join(text[-lines:]) or "(tester log is empty)"

"""Compile an .mq5 file with MetaEditor's command line."""
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import config
from mt5files import read_text_any

_RESULT_RE = re.compile(r"Result:\s*(\d+)\s+errors?,\s*(\d+)\s+warnings?", re.IGNORECASE)


@dataclass
class CompileResult:
    ok: bool
    errors: int
    warnings: int
    log: str
    ex5: Optional[Path]


def compile_ea(source: Path) -> CompileResult:
    """Blocking. Run it with asyncio.to_thread from async code."""
    source = Path(source)
    ex5 = source.with_suffix(".ex5")
    log_path = config.WORK_DIR / f"{source.stem}_compile.log"
    ex5.unlink(missing_ok=True)
    log_path.unlink(missing_ok=True)

    # The command line is built as ONE string on purpose: MetaEditor expects
    # /compile:"path with spaces" and subprocess's list quoting breaks that.
    cmd = (
        f'"{config.METAEDITOR_EXE}" /portable '
        f'/compile:"{source}" /log:"{log_path}"'
    )
    try:
        subprocess.run(cmd, timeout=config.COMPILE_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return CompileResult(False, -1, 0, "MetaEditor timed out while compiling.", None)

    log = read_text_any(log_path) if log_path.exists() else "(MetaEditor wrote no log)"
    match = _RESULT_RE.search(log)
    errors = int(match.group(1)) if match else -1
    warnings = int(match.group(2)) if match else 0
    ok = errors == 0 and ex5.exists()
    return CompileResult(ok, errors, warnings, log, ex5 if ok else None)

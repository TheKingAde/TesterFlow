"""Job data structures and the caption / config text parser.

Config syntax (used both as a file caption and as a plain text message):

    symbol=XAUUSD tf=H1 from=2025.01.01 to=2025.10.01 deposit=10000 model=1
    run: Lots=0.1 RR=2
    run tight: Lots=0.2 RR=3 Comment="my test"

Lines without the "run" prefix set tester settings for every run.
Each "run" line is one backtest with those EA input overrides.
No "run" lines means a single run with the EA's default inputs.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import config

KV = re.compile(r'([A-Za-z_][\w.]*)\s*=\s*(?:"([^"]*)"|([^\s,;]+))')
RUN_LINE = re.compile(r"^\s*run\s*([\w\-]*)\s*:\s*(.*)$", re.IGNORECASE)
DATE_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")

SETTING_ALIASES = {
    "symbol": "symbol",
    "tf": "period",
    "timeframe": "period",
    "period": "period",
    "from": "from_date",
    "start": "from_date",
    "to": "to_date",
    "end": "to_date",
    "deposit": "deposit",
    "balance": "deposit",
    "leverage": "leverage",
    "model": "model",
    "currency": "currency",
}

VALID_PERIODS = {
    "M1", "M2", "M3", "M4", "M5", "M6", "M10", "M12", "M15", "M20", "M30",
    "H1", "H2", "H3", "H4", "H6", "H8", "H12", "D1", "W1", "MN1",
}


@dataclass
class Run:
    label: str
    inputs: dict


@dataclass
class Submission:
    id: str
    chat_id: int
    user_id: int
    ea_name: str            # unique name, also the file stem in Experts\BacktestBot
    source_path: Path
    settings: dict
    runs: list
    cancelled: bool = False
    state: str = "queued"   # queued | compiling | running | done


def _validate(settings: dict) -> list:
    errors = []
    settings["period"] = settings["period"].upper()
    if settings["period"] not in VALID_PERIODS:
        errors.append(f"Bad timeframe '{settings['period']}'")
    if settings["model"] not in {"0", "1", "2", "3", "4"}:
        errors.append("model must be 0, 1, 2, 3 or 4")
    for key in ("from_date", "to_date"):
        if not DATE_RE.match(settings[key]):
            errors.append(f"{key} must look like 2025.01.31 (got '{settings[key]}')")
    for key in ("deposit", "leverage"):
        try:
            float(settings[key])
        except ValueError:
            errors.append(f"{key} must be a number (got '{settings[key]}')")
    if settings["from_date"] > settings["to_date"] and not errors:
        errors.append("from date is after to date")
    return errors


def parse_config(text: str):
    """Return (settings, runs, errors) for a caption / config message."""
    settings = dict(config.TESTER_DEFAULTS)
    runs = []
    errors = []

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = RUN_LINE.match(line)
        if match:
            label, body = match.group(1), match.group(2)
            inputs = {k: (quoted or plain) for k, quoted, plain in KV.findall(body)}
            runs.append(Run(label=label, inputs=inputs))
            continue
        for key, quoted, plain in KV.findall(line):
            target = SETTING_ALIASES.get(key.lower())
            if target is None:
                errors.append(
                    f"Unknown setting '{key}' (EA inputs go on a 'run:' line)"
                )
            else:
                settings[target] = quoted or plain

    errors.extend(_validate(settings))
    if not runs:
        runs = [Run(label="", inputs={})]
    return settings, runs, errors

"""Parse the Strategy Tester HTML report into key stats."""
import re
from pathlib import Path

from bs4 import BeautifulSoup

from mt5files import read_text_any

# (label as printed in the report, label shown in Telegram)
WANTED = [
    ("Total Net Profit", "Net profit"),
    ("Profit Factor", "Profit factor"),
    ("Total Trades", "Trades"),
    ("Profit Trades (% of total)", "Winners"),
    ("Expected Payoff", "Expected payoff"),
    ("Recovery Factor", "Recovery factor"),
    ("Sharpe Ratio", "Sharpe"),
    ("Balance Drawdown Maximal", "Max balance DD"),
    ("Equity Drawdown Maximal", "Max equity DD"),
    ("History Quality", "History quality"),
]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_report(html_path: Path) -> dict:
    """Return {label: value} for every 'Label:' cell followed by a value cell."""
    soup = BeautifulSoup(read_text_any(html_path), "html.parser")
    stats = {}
    for cell in soup.find_all("td"):
        label = _clean(cell.get_text())
        if not label.endswith(":") or len(label) > 60:
            continue
        sibling = cell.find_next_sibling("td")
        if sibling is None:
            continue
        stats.setdefault(label[:-1].strip(), _clean(sibling.get_text()))
    return stats


def format_summary(tag: str, ea_name: str, settings: dict, run_inputs: dict,
                   stats: dict) -> str:
    lines = [
        f"✅ Run {tag} — {ea_name}",
        f"{settings['symbol']} {settings['period']}  "
        f"{settings['from_date']} → {settings['to_date']}  "
        f"deposit {settings['deposit']} {settings['currency']}  model {settings['model']}",
    ]
    if run_inputs:
        lines.append("Inputs: " + ", ".join(f"{k}={v}" for k, v in run_inputs.items()))
    lines.append("")
    for key, shown in WANTED:
        lines.append(f"{shown}: {stats.get(key, 'n/a')}")
    return "\n".join(lines)

"""Telegram front end + sequential backtest queue.

Run on the Windows VPS:  python bot.py
"""
import asyncio
import logging
import re
import subprocess
import time
import uuid
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import syncer
from compiler import compile_ea
from jobs import Run, Submission, parse_config
from mt5files import parse_inputs, read_text_any, write_set_file, write_tester_ini
from report import format_summary, parse_report
from runner import (
    TesterRunner,
    collect_outputs,
    journal_since,
    journal_tail,
    saved_account_summary,
    terminal_running,
    tester_log_tail,
    warm_up,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("backtest-bot")

QUEUE: "asyncio.Queue[Submission]" = asyncio.Queue()
PENDING: list = []                 # submissions waiting or running (for /queue)
RUNNER = TesterRunner()
LAST_INPUTS: dict = {}             # user_id -> inputs parsed from their last EA
SAVED_CONFIG: dict = {}            # user_id -> (settings, runs) from a text message
STATE = {"login_ok": False}        # True once the backtest terminal has connected

HELP = """MT5 backtest bot

Send a .mq5 file. Put the config in the caption (max 1024 chars), or send the
same text as a normal message first and it applies to every upload after it.

symbol=XAUUSD tf=H1 from=2025.01.01 to=2025.10.01 deposit=10000 model=1
run: Lots=0.1 RR=2
run tight: Lots=0.2 RR=3

Lines without 'run' set tester settings. Each 'run' line is one backtest with
those EA inputs. No run lines = one run with default inputs.
model: 0 every tick, 1 1-min OHLC, 2 open prices, 4 real ticks

/queue  show what is running and waiting
/cancel stop the running backtest and skip the rest of that file
/clear  drop everything still waiting
/inputs list the inputs found in your last uploaded EA
/config show your saved config      /resetconfig  forget it
/login  connect the backtest terminal to your broker and report what happens
/diag   show the account the backtest terminal will use and its journal
/source show where includes are copied from before each compile
/setsource <path>  set that MQL5 folder (or the terminal data folder that
        contains it); '/setsource off' disables syncing"""


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
def authorised(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id in config.ALLOWED_USER_IDS)


def safe_name(stem: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "_", stem)[:40] or "EA"


def trim(text: str, limit: int = 3500) -> str:
    return text if len(text) <= limit else "…" + text[-limit:]


def account_configured() -> bool:
    """True if the tester has an account: set in .env, or saved by a manual login."""
    if config.MT5_LOGIN and config.MT5_SERVER:
        return True
    ini = config.TERMINAL_DIR / "config" / "common.ini"
    try:
        text = read_text_any(ini)
    except OSError:
        return False
    match = re.search(r"(?im)^\s*Login\s*=\s*(\d+)", text)
    return bool(match and int(match.group(1)) > 0)


def diagnose(log_text: str) -> str:
    """Plain-English hint for the most common 'no report' causes."""
    low = log_text.lower()
    hints = []
    if "not synchronized" in low:
        hints.append(
            "The backtest terminal was not connected to a broker server when the test "
            "started. Send /login to see exactly why it cannot connect."
        )
    if "account is not specified" in low:
        hints.append(
            "No account is saved in the backtest terminal. Send /login, or set "
            "MT5_LOGIN / MT5_SERVER / MT5_PASSWORD in .env."
        )
    if "cannot select symbol" in low and "not synchronized" not in low:
        hints.append(
            "The symbol is not available in the backtest terminal's Market Watch. "
            "Check the exact broker symbol name (e.g. XAUUSD.m or GOLD) and use it "
            "in the config: symbol=<name>."
        )
    return "\n\n".join(hints)


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if authorised(update):
        await update.message.reply_text(HELP)


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorised(update):
        return
    if not PENDING:
        await update.message.reply_text("Queue is empty.")
        return
    rows = []
    for i, sub in enumerate(PENDING, 1):
        rows.append(f"{i}. {sub.ea_name} — {len(sub.runs)} run(s) — {sub.state}")
    await update.message.reply_text("\n".join(rows))


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorised(update):
        return
    running = next((s for s in PENDING if s.state in ("compiling", "running")), None)
    if running is None:
        await update.message.reply_text("Nothing is running.")
        return
    running.cancelled = True
    RUNNER.cancel()
    await update.message.reply_text(f"Cancelling {running.ea_name}…")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorised(update):
        return
    count = 0
    for sub in PENDING:
        if sub.state == "queued":
            sub.cancelled = True
            count += 1
    await update.message.reply_text(f"Dropped {count} waiting submission(s).")


async def cmd_inputs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorised(update):
        return
    inputs = LAST_INPUTS.get(update.effective_user.id)
    if not inputs:
        await update.message.reply_text("Upload an EA first.")
        return
    text = "\n".join(f"{k} = {v}" for k, v in inputs.items())
    await update.message.reply_text(trim(text))


async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorised(update):
        return
    saved = SAVED_CONFIG.get(update.effective_user.id)
    if not saved:
        await update.message.reply_text("No saved config. Defaults from .env apply.")
        return
    settings, runs = saved
    lines = [f"{k}={v}" for k, v in settings.items()]
    lines += [f"run {r.label}: " + " ".join(f"{k}={v}" for k, v in r.inputs.items())
              for r in runs]
    await update.message.reply_text("\n".join(lines))


async def cmd_resetconfig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if authorised(update):
        SAVED_CONFIG.pop(update.effective_user.id, None)
        await update.message.reply_text("Saved config cleared.")


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Connect the backtest terminal to the broker now and report the result."""
    if not authorised(update):
        return
    if any(s.state in ("compiling", "running") for s in PENDING):
        await update.message.reply_text("A backtest is running. Try /login when it is done.")
        return
    await update.message.reply_text(
        "Starting the backtest terminal and waiting for it to connect "
        "(up to 4 minutes on a slow machine)…"
    )
    ok, report = await asyncio.to_thread(warm_up)
    STATE["login_ok"] = ok
    await update.message.reply_text(trim(report))


async def cmd_diag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show which account the backtest terminal would use, and its recent journal."""
    if not authorised(update):
        return
    running = await asyncio.to_thread(terminal_running)
    text = (
        f".env login: {config.MT5_LOGIN or '(not set)'}\n"
        f".env server: {config.MT5_SERVER or '(not set)'}\n"
        f".env password: {'set' if config.MT5_PASSWORD else 'not set'}\n"
        f"Saved in backtest terminal: {saved_account_summary()}\n"
        f"terminal64.exe running now: {'YES' if running else 'no'}\n"
        f"Connected this session: {'yes' if STATE['login_ok'] else 'not yet'}\n\n"
        "TERMINAL JOURNAL " + journal_tail(30)
    )
    await update.message.reply_text(trim(text))


async def cmd_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorised(update):
        return
    source = syncer.get_source()
    extra = f", Experts\\{config.SYNC_EXPERTS_SUBDIR}\\*.mqh" if config.SYNC_EXPERTS_SUBDIR else ""
    await update.message.reply_text(
        f"Include source: {source if source else 'not set'}\n"
        f"Backtest terminal: {config.MQL5_DIR}\n"
        f"Synced: Include, Libraries, Indicators{extra}\n"
        f"Last sync: {syncer.LAST_SYNC}\n"
        f"Account configured: {'yes' if account_configured() else 'NO'}"
    )


async def cmd_setsource(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorised(update):
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await update.message.reply_text(
            "Usage: /setsource C:\\Users\\Administrator\\AppData\\Roaming\\MetaQuotes"
            "\\Terminal\\<hash>\\MQL5\nor /setsource off"
        )
        return
    ok, message = syncer.set_source(raw)
    await update.message.reply_text(("✅ " if ok else "❌ ") + message)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A plain text message in config syntax becomes the saved config."""
    if not authorised(update):
        return
    settings, runs, errors = parse_config(update.message.text)
    if errors:
        await update.message.reply_text("Config problems:\n- " + "\n- ".join(errors))
        return
    SAVED_CONFIG[update.effective_user.id] = (settings, runs)
    await update.message.reply_text(
        f"Config saved: {len(runs)} run(s) per upload. "
        "It applies to uploads without a caption. /config to review."
    )


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorised(update):
        return
    msg = update.message
    doc = msg.document
    name = doc.file_name or ""
    if not name.lower().endswith(".mq5"):
        await msg.reply_text("Please send the .mq5 source file.")
        return

    if (msg.caption or "").strip():
        settings, runs, errors = parse_config(msg.caption)
    elif update.effective_user.id in SAVED_CONFIG:
        settings, runs = SAVED_CONFIG[update.effective_user.id]
        errors = []
    else:
        settings, runs, errors = parse_config("")
    if errors:
        await msg.reply_text("Config problems:\n- " + "\n- ".join(errors))
        return

    sub_id = uuid.uuid4().hex[:6]
    unique = f"{safe_name(Path(name).stem)}_{sub_id}"
    dest = config.EXPERTS_DIR / f"{unique}.mq5"
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(custom_path=dest)

    known = parse_inputs(read_text_any(dest))
    LAST_INPUTS[update.effective_user.id] = known
    unknown = sorted({k for r in runs for k in r.inputs if known and k not in known})

    sub = Submission(
        id=sub_id,
        chat_id=msg.chat_id,
        user_id=update.effective_user.id,
        ea_name=unique,
        source_path=dest,
        settings=settings,
        runs=runs,
    )
    PENDING.append(sub)
    await QUEUE.put(sub)

    note = f"Queued #{len(PENDING)}: {unique} — {len(runs)} run(s)."
    if unknown:
        note += (
            "\n⚠️ Not found among the EA's inputs: " + ", ".join(unknown)
            + "\n(The tester silently ignores unknown names. Check /inputs.)"
        )
    await msg.reply_text(note)


# --------------------------------------------------------------------------
# Worker: one submission at a time, compile once, then each run in order
# --------------------------------------------------------------------------
async def process(app: Application, sub: Submission):
    bot = app.bot
    say = lambda text: bot.send_message(sub.chat_id, text)

    sub.state = "compiling"
    try:
        await asyncio.to_thread(syncer.sync_sources)
        await say(f"📂 Include sync: {syncer.LAST_SYNC}")
    except Exception as exc:  # a failed sync should not block the backtest
        await say(f"⚠️ Include sync failed: {exc}")
    await say(f"⚙️ Compiling {sub.ea_name}…")
    result = await asyncio.to_thread(compile_ea, sub.source_path)
    if not result.ok:
        await say("❌ Compile failed:\n" + trim(result.log))
        return
    if result.warnings:
        await say(f"Compiled with {result.warnings} warning(s).")

    if await asyncio.to_thread(terminal_running):
        await say(
            "⚠️ A terminal64.exe is already running. If it is the backtest terminal "
            "(C:\\MT5_Backtest) close it, or the tester launch will not work."
        )

    # Connect once per bot session. The tester only waits about a minute for a
    # connection, so on a slow machine it must be connected beforehand.
    if not STATE["login_ok"]:
        await say(
            "🔌 Connecting the backtest terminal to your broker "
            "(the first time can take a few minutes)…"
        )
        ok, report = await asyncio.to_thread(warm_up)
        STATE["login_ok"] = ok
        await say(trim(report))
        if not ok:
            await say("Stopped before running the test. Fix the login, then resend the EA.")
            return

    sub.state = "running"
    total = len(sub.runs)
    for i, run in enumerate(sub.runs, 1):
        if sub.cancelled:
            await say("Cancelled. Skipping remaining runs.")
            return
        tag = f"{i}/{total}" + (f" ({run.label})" if run.label else "")
        stem = f"{sub.id}_{i}"

        set_name = ""
        if run.inputs:
            set_name = f"{stem}.set"
            write_set_file(config.PROFILES_TESTER / set_name, run.inputs)
        ini_path = config.WORK_DIR / f"{stem}.ini"
        write_tester_ini(
            ini_path,
            expert_rel=f"{config.EXPERT_REL_PREFIX}\\{sub.ea_name}",
            set_name=set_name,
            settings=sub.settings,
            report_rel=f"{config.REPORT_REL_PREFIX}\\{stem}",
        )
        # kept for the failure message below, with the password masked
        ini_preview = re.sub(
            r"(?im)^(Password=).*$", r"\1***", ini_path.read_text(encoding="utf-8")
        )

        await say(f"▶️ Running {tag}…")
        started = time.time()
        try:
            await asyncio.to_thread(RUNNER.run, ini_path, config.TEST_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            await say(f"⏱ Run {tag} timed out and was killed.")
            continue
        finally:
            if config.MT5_PASSWORD:
                ini_path.unlink(missing_ok=True)  # the ini holds the account password
        if sub.cancelled:
            await say("Cancelled. Skipping remaining runs.")
            return

        outputs = collect_outputs(stem)
        if outputs.html is None:
            tail = tester_log_tail()
            if "not synchronized" in tail.lower():
                STATE["login_ok"] = False  # reconnect before the next submission
            await say(
                f"❌ Run {tag} produced no report. Last tester log lines:\n" + trim(tail)
            )
            await say(
                "Terminal journal for this run only:\n" + trim(journal_since(started))
            )
            hint = diagnose(tail)
            if hint:
                await say("💡 " + hint)
            await say("Tester config used (password hidden):\n" + ini_preview)
            continue

        stats = parse_report(outputs.html)
        await say(format_summary(tag, sub.ea_name, sub.settings, run.inputs, stats))
        if outputs.png:
            with open(outputs.png, "rb") as fh:
                await bot.send_photo(sub.chat_id, photo=fh, caption=f"Balance/equity — {tag}")
        else:
            await say("(No chart image found next to the report.)")
        with open(outputs.html, "rb") as fh:
            await bot.send_document(
                sub.chat_id, document=fh, filename=f"{sub.ea_name}_{i}.html"
            )

    sub.state = "done"
    await say(f"🏁 Finished {sub.ea_name}.")


async def worker(app: Application):
    while True:
        sub = await QUEUE.get()
        try:
            if sub.cancelled:
                continue
            await process(app, sub)
        except Exception as exc:  # keep the queue alive whatever happens
            log.exception("submission %s failed", sub.id)
            try:
                await app.bot.send_message(sub.chat_id, f"❌ Internal error: {exc}")
            except Exception:
                pass
        finally:
            if sub in PENDING:
                PENDING.remove(sub)
            QUEUE.task_done()


async def post_init(app: Application):
    app.create_task(worker(app))
    log.info("Worker started. Allowed users: %s", sorted(config.ALLOWED_USER_IDS))


def main():
    app = ApplicationBuilder().token(config.BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("inputs", cmd_inputs))
    app.add_handler(CommandHandler("config", cmd_config))
    app.add_handler(CommandHandler("resetconfig", cmd_resetconfig))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("diag", cmd_diag))
    app.add_handler(CommandHandler("source", cmd_source))
    app.add_handler(CommandHandler("setsource", cmd_setsource))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.run_polling()


if __name__ == "__main__":
    main()
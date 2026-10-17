# MT5 Backtest Bot

Telegram bot that runs on your Windows VPS. You send an `.mq5` file; it compiles it
with MetaEditor, runs the MT5 Strategy Tester from the command line (one or more runs
with different inputs), and sends back the stats, the balance/equity chart and the
full HTML report. Submissions queue and run one at a time.

## One-time VPS setup

1. Install Python 3.10+ and run `pip install -r requirements.txt`.
2. Make a **dedicated copy** of MetaTrader 5 (e.g. `C:\MT5_Backtest`), separate from any
   live terminal. The bot always starts it with `/portable`, so its data folder is the
   install folder itself.
3. Start that copy once by hand with `terminal64.exe /portable`, log in to your broker
   account, tick "Save account info", and let it download history for your symbols.
   (First backtests on a new symbol/period are slow while ticks sync.)
4. Create a bot with @BotFather and get your numeric user ID (e.g. from @userinfobot).
5. Copy `.env.example` to `.env` and fill in the token, your user ID and `TERMINAL_DIR`.
6. `python bot.py`. To keep it running after you close RDP, use NSSM or Task Scheduler
   ("run at startup").

## Custom includes and libraries

The compiler only searches the backtest terminal's own `MQL5` folder (because of
`/portable`), not the hashed `AppData\Roaming\MetaQuotes\Terminal\<HASH>` folder of your
everyday terminal. Set `SYNC_SOURCE_MQL5` in `.env` to that terminal's `MQL5` folder and
the bot copies newer files into the backtest terminal before every compile:

- `Include\`, `Libraries\`, `Indicators\` go to the same folders in the backtest terminal
  (so `#include <MyLib\x.mqh>` and `iCustom` indicators work).
- `Experts\<SYNC_EXPERTS_SUBDIR>\*.mqh` goes to `Experts\BacktestBot\` (so
  `#include "x.mqh"` next to the EA works). Default subfolder is `Advisors`.

Sync only adds or updates files; it never deletes anything.

## Usage

Send a `.mq5` file with a caption (max 1024 characters), or send the config as a normal
text message first and it applies to every upload after it.

```
symbol=XAUUSD tf=H1 from=2025.01.01 to=2025.10.01 deposit=10000 model=1
run: Lots=0.1 RR=2
run tight: Lots=0.2 RR=3
```

- Lines without `run` set tester settings for all runs.
- Each `run` line is one backtest with those EA input overrides.
- No `run` lines: a single run with the EA's default inputs.
- Commands: `/queue`, `/cancel`, `/clear`, `/inputs`, `/config`, `/resetconfig`.

## Includes and libraries (your .mqh files)

Your real terminal keeps its MQL5 folder under a hashed AppData path. The backtest
terminal is a separate portable copy, so it does not see those files by itself. Set the
source path and the bot copies them over before every compile:

- In `.env`: `SOURCE_MQL5_DIR=C:\Users\...\MetaQuotes\Terminal\<hash>\MQL5`
- Or from Telegram at any time: `/setsource <path>` (`/source` shows the current value,
  `/setsource off` disables it). The Telegram value overrides `.env` and is remembered.

It copies only new or changed files from `Include`, `Libraries` and `Indicators`
(`SYNC_SUBDIRS` in `.env` changes that list), plus any `.mqh` files sitting in the source's
`Experts\Advisors` folder, so `#include "File.mqh"` (quotes) also resolves. You can paste
either the `...\MQL5` folder or the data folder that contains it.

## How each run works

1. The upload is saved to `MQL5\Experts\BacktestBot\<name>_<id>.mq5`.
2. `MetaEditor64.exe /portable /compile:"..." /log:"..."` builds the `.ex5`; the log is
   parsed for `Result: N errors`.
3. For each run a `.set` file (UTF-16, `Name=Value` lines) is written to
   `MQL5\Profiles\Tester` and a tester `.ini` is written to `work\`.
4. `terminal64.exe /portable /config:"<ini>"` runs the test; `ShutdownTerminal=1`
   makes the terminal exit when it finishes.
5. The report `reports\<id>_<n>.htm` and chart `.png` are picked up, parsed, and sent.

## Things to verify on your first run

I could not run MetaTrader here, so test with one short date range first:

- **"tester not started because the account is not specified"** in the tester log: the
  backtest terminal has no saved account. Log in by hand while it is started with
  `/portable` (afterwards `C:\MT5_Backtest\config\common.ini` should contain your login),
  or set `MT5_LOGIN`, `MT5_SERVER` (and optionally `MT5_PASSWORD`) in `.env` so the bot
  passes the account to the tester itself.
- **Report file name/extension**: expect `reports\<id>_1.htm` plus a `.png`. If the bot
  says "produced no report", check what landed in `reports\` and the tester log lines it
  prints.
- **Chart PNG**: the bot sends it if MT5 writes one next to the report. If yours does
  not, tell me and I will add a matplotlib equity curve built from the report's deals table.
- **Report labels**: the stats parser reads English labels ("Total Net Profit:"). A terminal
  in another language will show `n/a`; the HTML report is still attached.
- **Inputs**: the bot only warns about unknown input names (it finds inputs by regex on
  the source). The tester silently ignores wrong names, so check `/inputs`.
- **EAs with custom `#include` files** will fail to compile unless the includes are
  already in `MQL5\Include` of the backtest terminal.

## Security

The bot compiles and runs whatever it is sent. Keep `ALLOWED_USER_IDS` to yourself, run
it on a VPS with nothing else valuable on it, and do not leave a funded live account
logged in on the backtest terminal.

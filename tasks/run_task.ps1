# Signal monitor: called every 5 minutes by Task Scheduler.
# engine\run.py decides by Beijing time: 14:30-14:59 intraday, after 15:35 close; skips if already done today.
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
Set-Location $repo
$py = "C:\Users\cindy\AppData\Local\Programs\Python\Python312\python.exe"
$env:PYTHONUTF8 = "1"
& $py "engine\run.py" *>> "cache\task.log"
# Nightly local backtest of saved combos (runs once after today's close review; exits at once otherwise)
& $py "engine\bt_nightly.py" --auto *>> "cache\task.log"

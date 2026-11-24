# Register the "SignalMonitor" scheduled task.
# Runs every 5 minutes from Beijing 13:25 for 4 hours (covers 14:30 intraday and 15:35 close even after a DST shift).
# The Python side decides what to do, so extra runs exit in a second.
# Run once (normal PowerShell):
#   powershell -ExecutionPolicy Bypass -File D:\signal-monitor\tasks\setup_tasks.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$script = Join-Path $here "run_task.ps1"
$taskName = "SignalMonitor"

$offset = [System.TimeZoneInfo]::Local.GetUtcOffset((Get-Date))
$bj = (Get-Date -Hour 13 -Minute 25 -Second 0)
$start = $bj.AddHours(-8).Add($offset)

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At $start.ToString("HH:mm")
$rep = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Hours 4)
$trigger.Repetition = $rep.Repetition
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 40) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Strategy signal monitor (intraday 14:30 / close 15:35 Beijing)" -Force | Out-Null

Write-Host ("Machine timezone : " + [System.TimeZoneInfo]::Local.Id + " (UTC" + $offset.ToString() + ")")
Write-Host ("Beijing 13:25 == local " + $start.ToString("HH:mm") + ", every 5 min for 4 hours")
Write-Host ("Registered '" + $taskName + "'.")

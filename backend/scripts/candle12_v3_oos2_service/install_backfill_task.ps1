# Register the CANDLE12_V3 OOS-2 scheduled BTCUSDT funding/candle backfill.
#
# Task:    CANDLE12_V3_OOS2_FundingBackfill
# Action:  backend\.venv\Scripts\pythonw.exe scripts\candle12_v3_oos2_service\scheduled_backfill.py
#          (working directory backend\). The wrapper runs the documented
#          `backfill-data --symbols BTCUSDT --start 2026-09-04T08:00:00+00:00 --end now`.
# Times:   daily at 00:20, 08:20 and 16:20 UTC. The start boundaries end in "Z"
#          (Task Scheduler's "synchronize across time zones"), so they do not depend on the
#          Windows time zone. On this machine (IST, UTC+05:30) that is 05:50, 13:50, 21:50.
# Missed:  StartWhenAvailable. A start missed because the machine was off or asleep runs
#          as soon as possible.
# Retries: the wrapper makes up to 3 attempts, 5 min apart. The task restarts once
#          more after 15 min if the wrapper itself fails. ExecutionTimeLimit is 75 min.
# Overlap: MultipleInstances IgnoreNew, plus the wrapper's own OS file lock.
#
# Without admin rights the task runs as this user only while logged on (Interactive).
# From an elevated PowerShell, -S4U registers it to run whether or not the user is logged
# on. S4U needs no stored password; the task reads D:\cryptoscreener\.env (DATABASE_URL)
# as this user and reaches localhost PostgreSQL and the public Binance API.
param([switch]$S4U)

$Name = "CANDLE12_V3_OOS2_FundingBackfill"
$Backend = "D:\cryptoscreener\backend"
$Exe = "$Backend\.venv\Scripts\pythonw.exe"
$Arg = "scripts\candle12_v3_oos2_service\scheduled_backfill.py"

if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
    Write-Output "task $Name already exists; updating its definition in place (no duplicate is created)"
}
$day = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd")
$triggers = foreach ($hhmm in "00:20", "08:20", "16:20") {
    $t = New-ScheduledTaskTrigger -Daily -At "00:00"
    $t.StartBoundary = "${day}T${hhmm}:00Z"   # UTC-anchored
    $t
}
$action = New-ScheduledTaskAction -Execute $Exe -Argument $Arg -WorkingDirectory $Backend
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$logon = if ($S4U) { "S4U" } else { "Interactive" }
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType $logon -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 75) -RestartCount 1 -RestartInterval (New-TimeSpan -Minutes 15) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $Name -Action $action -Trigger $triggers -Principal $principal -Settings $settings `
    -Description "CANDLE12_V3_EXEC_FIX/OOS-2: BTCUSDT funding/candle backfill at 00:20/08:20/16:20 UTC (insert-only)" -Force | Out-Null
# PowerShell writes the boundaries in local time. Rewrite them as UTC ("Z") and set normal
# priority (4). Task Scheduler's default of 7 (below normal) starved the backfill under
# sustained 100% CPU on 2026-10-16: 62 ms of CPU in 6 minutes.
$xml = [xml](Export-ScheduledTask -TaskName $Name)
$hh = @("00:20", "08:20", "16:20"); $i = 0
foreach ($t in $xml.Task.Triggers.CalendarTrigger) { $t.StartBoundary = "${day}T$($hh[$i]):00Z"; $i++ }
$xml.Task.Settings.Priority = "4"
Register-ScheduledTask -TaskName $Name -Xml $xml.OuterXml -Force | Select-Object TaskName, State

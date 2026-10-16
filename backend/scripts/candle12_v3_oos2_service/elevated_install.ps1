# One elevated step: switch the CANDLE12_V3 OOS-2 runner task to start at boot (S4U, normal
# priority), and make the funding backfill task run without a logon (S4U).
# Run from an elevated PowerShell:
#   powershell -NoProfile -ExecutionPolicy Bypass -File D:\cryptoscreener\backend\scripts\candle12_v3_oos2_service\elevated_install.ps1
# -DryRun runs every check, the backups and the Interactive diagnostic, but registers
# nothing. It needs no elevation.
#
# Order of operations:
#   1. Export both current task definitions to service_logs\task_backups\<UTC>\.
#   2. Record the live runner (PIDs, run lock).
#   3. Run the unattended diagnostic in a temporary S4U task. If anything fails, stop.
#      The working Interactive tasks are left as they are.
#   4. Re-register CANDLE12_V3_OOS2_Runner with install_task.ps1 -AtStartup -Priority 4,
#      and CANDLE12_V3_OOS2_FundingBackfill with install_backfill_task.ps1 -S4U. Names,
#      supervisor, --recover, watchdog, working directory, interpreter, store and
#      single-instance rules are unchanged. Re-registering does not stop a running
#      instance (tested 2026-10-16 with a probe task).
#   5. Verify: a boot trigger on the runner task, S4U on both tasks, task priority 4,
#      and the same runner still alive and alone.
# To restore: Register-ScheduledTask -Xml (Get-Content <backup>.xml -Raw) -TaskName <name> -Force
param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$Svc = "D:\cryptoscreener\backend\scripts\candle12_v3_oos2_service"
$Logs = "D:\cryptoscreener\backend\data\candle12_v3_paper\service_logs"
$Store = "D:\cryptoscreener\backend\data\candle12_v3_paper\virtual_paper_mainnet\oos2"
$Tasks = "CANDLE12_V3_OOS2_Runner", "CANDLE12_V3_OOS2_FundingBackfill"
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $DryRun -and -not $admin) { Write-Output "STOP: not elevated. Open PowerShell with 'Run as administrator'."; exit 2 }

function Runners {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object {
        $_.CommandLine -like "*app.research.candle12_v3_paper.cli run --mode virtual_paper_mainnet --store data/candle12_v3_paper/virtual_paper_mainnet/oos2 --recover*" }
}

# 1. backups
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$bdir = Join-Path $Logs "task_backups\$stamp"
New-Item -ItemType Directory -Force $bdir | Out-Null
foreach ($t in $Tasks) { Export-ScheduledTask -TaskName $t | Out-File -Encoding utf8 (Join-Path $bdir "$t.xml") }
Write-Output "1. backups: $bdir ($((Get-ChildItem $bdir).Count) files)"

# 2. live runner
$lock = Get-Content "$Store\runner.lock" -Raw | ConvertFrom-Json
$r0 = @(Runners)
Write-Output "2. runner before: lock pid=$($lock.pid); processes=$(($r0 | ForEach-Object { $_.ProcessId }) -join ',')"
if ($r0.Count -ne 2 -or -not ($r0 | Where-Object ProcessId -eq $lock.pid)) { Write-Output "STOP: expected exactly one runner (launcher + interpreter) holding the lock"; exit 3 }

# 3. unattended diagnostic
$diag = & "$Svc\run_unattended_diag.ps1" -S4U:(-not $DryRun)
$diag | ForEach-Object { Write-Output "   $_" }
if (-not ($diag -match "all_ok=True")) { Write-Output "STOP: unattended diagnostic did not pass; tasks left unchanged (Interactive)."; exit 4 }
Write-Output "3. diagnostic passed ($(if ($DryRun) { 'Interactive, dry run' } else { 'S4U' }))"

if ($DryRun) { Write-Output "DRY RUN: would now run install_task.ps1 -AtStartup -Priority 4 and install_backfill_task.ps1 -S4U"; exit 0 }

# 4. re-register in place
& "$Svc\install_task.ps1" -AtStartup -Priority 4 | Out-Null
& "$Svc\install_backfill_task.ps1" -S4U | Out-Null

# 5. verify
$ok = $true
$rx = [xml](Export-ScheduledTask -TaskName "CANDLE12_V3_OOS2_Runner")
$bx = [xml](Export-ScheduledTask -TaskName "CANDLE12_V3_OOS2_FundingBackfill")
$boot = [bool]($rx.Task.Triggers.ChildNodes | Where-Object { $_.LocalName -eq "BootTrigger" })
$watch = [bool]($rx.Task.Triggers.ChildNodes | Where-Object { $_.LocalName -eq "TimeTrigger" })
Write-Output ("5. runner task: boot_trigger={0} watchdog={1} logon={2} priority={3} action={4} {5}" -f $boot, $watch,
    $rx.Task.Principals.Principal.LogonType, $rx.Task.Settings.Priority, $rx.Task.Actions.Exec.Command, $rx.Task.Actions.Exec.Arguments)
Write-Output ("   backfill task: logon={0} priority={1} boundaries={2}" -f $bx.Task.Principals.Principal.LogonType, $bx.Task.Settings.Priority,
    (($bx.Task.Triggers.CalendarTrigger | ForEach-Object StartBoundary) -join ","))
if (-not $boot -or -not $watch -or $rx.Task.Principals.Principal.LogonType -ne "S4U" -or $rx.Task.Settings.Priority -ne "4") { $ok = $false }
if ($bx.Task.Principals.Principal.LogonType -ne "S4U") { $ok = $false }
$r1 = @(Runners)
$same = (($r1 | ForEach-Object ProcessId | Sort-Object) -join ",") -eq (($r0 | ForEach-Object ProcessId | Sort-Object) -join ",")
Write-Output "   runner after: processes=$(($r1 | ForEach-Object ProcessId) -join ',') unchanged=$same"
if (-not $same) { $ok = $false }
Write-Output $(if ($ok) { "RESULT: INSTALLED (boot trigger configured; BOOT_RECOVERY_NOT_YET_TESTED)" } else { "RESULT: CHECK FAILED; restore from $bdir if needed" })

# Register the CANDLE12_V3 OOS-2 runner as a Task Scheduler task for the current user.
#
# Triggers:
#   - at logon of this user;
#   - every 5 minutes, indefinitely, as a watchdog. MultipleInstances=IgnoreNew plus
#     the supervisor's own OS lock mean a live supervisor is never doubled.
# Action: backend\.venv\Scripts\pythonw.exe scripts\candle12_v3_oos2_service\supervisor.py
#   (no console window; working directory backend\).
# The supervisor always starts the runner with --recover.
#
# Without admin rights the task runs only while this user is logged on (LogonType
# Interactive). To start at boot with no logon, run install_task.ps1 -AtStartup from
# an elevated PowerShell. It then registers with the S4U logon type.
param([switch]$AtStartup)

$Name = "CANDLE12_V3_OOS2_Runner"
$Backend = "D:\cryptoscreener\backend"
$Exe = "$Backend\.venv\Scripts\pythonw.exe"
$Arg = "scripts\candle12_v3_oos2_service\supervisor.py"

$action = New-ScheduledTaskAction -Execute $Exe -Argument $Arg -WorkingDirectory $Backend
$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($AtStartup) {
    $first = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
} else {
    $first = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
}
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd
Register-ScheduledTask -TaskName $Name -Action $action -Trigger @($first, $watchdog) -Principal $principal `
    -Settings $settings -Description "CANDLE12_V3_EXEC_FIX/OOS-2 virtual_paper_mainnet runner supervisor (always --recover)" -Force |
    Select-Object TaskName, State

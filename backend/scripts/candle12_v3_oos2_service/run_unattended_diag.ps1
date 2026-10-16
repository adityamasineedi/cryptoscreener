# Run unattended_diag.py once in a TEMPORARY scheduled task under the proposed identity and
# logon type, print the sanitized result, then delete the task.
#   run_unattended_diag.ps1          Interactive (current user, logged-on session)
#   run_unattended_diag.ps1 -S4U     S4U, no stored password. Needs an elevated PowerShell.
# The task is "CANDLE12_V3_OOS2_UnattendedDiag_TEMP". It runs at Normal priority (task
# priority 4) and never starts a runner or touches the OOS store. A successful
# diagnostic is not a reboot test.
param([switch]$S4U)

$Name = "CANDLE12_V3_OOS2_UnattendedDiag_TEMP"
$Backend = "D:\cryptoscreener\backend"
$Exe = "$Backend\.venv\Scripts\python.exe"
$Arg = "scripts\candle12_v3_oos2_service\unattended_diag.py"
$Logs = "$Backend\data\candle12_v3_paper\service_logs"

if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $Name -Confirm:$false }
$before = @(Get-ChildItem $Logs -Filter "unattended_diag_*.json" -ErrorAction SilentlyContinue | ForEach-Object Name)
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$logon = if ($S4U) { "S4U" } else { "Interactive" }
$action = New-ScheduledTaskAction -Execute $Exe -Argument $Arg -WorkingDirectory $Backend
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType $logon -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -Priority 4 `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
try {
    Register-ScheduledTask -TaskName $Name -Action $action -Principal $principal -Settings $settings `
        -Description "TEMPORARY diagnostic for CANDLE12_V3 OOS-2 unattended execution; deleted after one run" -ErrorAction Stop | Out-Null
} catch {
    Write-Output "REGISTER_FAILED logon=$logon : $($_.Exception.Message)"
    exit 2
}
try {
    Start-ScheduledTask -TaskName $Name
    $deadline = (Get-Date).AddMinutes(5)
    do { Start-Sleep 3; $state = (Get-ScheduledTask -TaskName $Name).State } while ($state -eq "Running" -and (Get-Date) -lt $deadline)
    Start-Sleep 2
    $info = Get-ScheduledTaskInfo -TaskName $Name
    Write-Output ("TASK logon={0} state={1} last_result={2} (0x{2:X8})" -f $logon, $state, $info.LastTaskResult)
    $new = Get-ChildItem $Logs -Filter "unattended_diag_*.json" | Where-Object { $before -notcontains $_.Name } | Sort-Object LastWriteTime | Select-Object -Last 1
    if ($new) {
        $r = Get-Content $new.FullName -Raw | ConvertFrom-Json
        Write-Output "RESULT file=$($new.Name) all_ok=$($r.all_ok)"
        $r.checks.PSObject.Properties | ForEach-Object { Write-Output ("  {0}: ok={1} {2}" -f $_.Name, $_.Value.ok, $_.Value.error) }
        Write-Output "  interpreter: $($r.checks.interpreter.executable) base=$($r.checks.interpreter.base_executable) user=$($r.checks.interpreter.user) session=$($r.checks.interpreter.session_name)"
    } else {
        Write-Output "RESULT none: the diagnostic wrote no result file (the interpreter likely failed to launch; see last_result)"
    }
} finally {
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    Write-Output "temporary task removed: $(-not (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue))"
}

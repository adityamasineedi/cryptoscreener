<#
.SYNOPSIS
  Register (or remove) the Task Scheduler task that brings live back after a reboot or a crash
  (owner rulings D46, D53, D128a; proposal P12). The owner runs it; nothing is registered by anyone else.

.DESCRIPTION
  Task "LIVE backend autostart (P12)":
    - triggers: at logon of the current user, and every 5 minutes (repeating indefinitely);
    - action: start_live.ps1 -Auto from the deploy worktree. It does nothing when live is already up, when the
      owner stopped it on purpose (data\live_stopped_by_owner), or while auto-start is paused after 3 auto-starts
      in 60 minutes (data\live_autostart_paused). So "restart on failure" = back within 5 minutes of a crash;
    - settings: start when available (after sleep), on battery too, never a second instance, 10 minute limit,
      Task Scheduler retries a failed run 3 times 1 minute apart.
  Default logon type is Interactive: it runs only while the owner is logged on (no password stored), like the
  forward-runner tasks. -AtStartup adds a boot trigger and runs whether or not anyone is logged on; Windows then
  needs the account password, which Register-ScheduledTask asks for (it is stored by Windows, never by us).

    powershell -ExecutionPolicy Bypass -File D:\cryptoscreener-deploy\scripts\live\register_autostart.ps1
    powershell -ExecutionPolicy Bypass -File D:\cryptoscreener-deploy\scripts\live\register_autostart.ps1 -DryRun
    powershell -ExecutionPolicy Bypass -File D:\cryptoscreener-deploy\scripts\live\register_autostart.ps1 -Unregister
    Get-ScheduledTask -TaskName "LIVE backend autostart (P12)" | Get-ScheduledTaskInfo

  The same task as XML (for schtasks /Create /XML) is live_autostart_task.xml next to this script.
  Exit codes: 0 ok, 2 bad argument, 3 refused.
#>
param(
    [string]$TaskName = 'LIVE backend autostart (P12)',
    [string]$DeployDir = 'D:\cryptoscreener-deploy',
    [string]$LiveRoot = 'D:\cryptoscreener',
    [int]$EveryMinutes = 5,
    [switch]$AtStartup,
    [switch]$Unregister,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')
if ($EveryMinutes -lt 1 -or $EveryMinutes -gt 60) { Stop-D128 $EXIT_BAD_ARG "-EveryMinutes must be 1..60" }
if ($TaskName -notmatch '^[A-Za-z0-9 ()._-]{3,100}$') { Stop-D128 $EXIT_BAD_ARG "-TaskName has unexpected characters" }

if ($Unregister) {
    if ($DryRun) { Stop-D128 $EXIT_OK "DRY RUN: would remove task '$TaskName'" }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Stop-D128 $EXIT_OK "removed task '$TaskName' (if it existed)"
}

$script = Join-Path (Get-FullPath $DeployDir) 'scripts\live\start_live.ps1'
if (-not $DryRun -and -not (Test-Path -LiteralPath $script)) {
    Stop-D128 $EXIT_REFUSED "$script not found. Deploy a commit that contains scripts\live first (start_live.ps1 -Commit ...)"
}
$arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -Auto " +
             "-LiveRoot `"$(Get-FullPath $LiveRoot)`" -DeployDir `"$(Get-FullPath $DeployDir)`""
$user = "$env:USERDOMAIN\$env:USERNAME"
if ($DryRun) {
    Write-Output "DRY RUN: nothing is registered."
    Write-Output "  task:      $TaskName"
    Write-Output "  action:    powershell.exe $arguments"
    Write-Output "  triggers:  at logon of $user; every $EveryMinutes min$(if ($AtStartup) { '; at startup' })"
    Write-Output "  logon:     $(if ($AtStartup) { 'Password (runs whether logged on or not; Windows asks for the password)' } else { 'Interactive (only while logged on)' })"
    exit $EXIT_OK
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory (Get-FullPath $DeployDir)
$triggers = @(New-ScheduledTaskTrigger -AtLogOn -User $user)
$rep = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)
$triggers += $rep
if ($AtStartup) { $triggers += New-ScheduledTaskTrigger -AtStartup }
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$desc = "P12: start the live backend from the pinned deploy worktree ($DeployDir) when it is down. Does nothing if live is up, " +
        "stopped by the owner (data\live_stopped_by_owner) or paused after a crash loop (data\live_autostart_paused)."
if ($AtStartup) {
    $cred = Get-Credential -UserName $user -Message "Password for '$TaskName' (stored by Windows Task Scheduler only)"
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings -RunLevel Limited `
        -User $cred.UserName -Password $cred.GetNetworkCredential().Password -Description $desc -Force | Out-Null
} else {
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings -Principal $principal `
        -Description $desc -Force | Out-Null
}
$info = Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo
Write-Output "registered: $TaskName (next run $($info.NextRunTime))"
exit $EXIT_OK

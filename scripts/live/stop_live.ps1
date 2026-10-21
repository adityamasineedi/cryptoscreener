<#
.SYNOPSIS
  Stop the live backend (owner rulings D46, D53, D128a; proposal P12). The owner runs it.

.DESCRIPTION
  Finds every app.main:app process tree (and anything listening on the port), sends CTRL+BREAK to each root so the
  app runs its shutdown (paper writes flushed, writer lock released), waits up to -TimeoutSeconds, then stops
  whatever is left. By default it writes data\live_stopped_by_owner so the auto-start task does not bring live
  back; start_live.ps1 (manual) removes that marker. deploy_live.ps1 and rollback_live.ps1 call this with
  -NoMarker.

    powershell -ExecutionPolicy Bypass -File D:\cryptoscreener-deploy\scripts\live\stop_live.ps1

  Exit codes: 0 stopped (or nothing ran), 2 bad argument, 4 something still runs after the forced stop.
#>
param(
    [string]$ProcessMatch = '',
    [int]$Port = 8000,
    [string]$LiveRoot = 'D:\cryptoscreener',
    [int]$TimeoutSeconds = 180,
    [switch]$NoMarker,
    [switch]$DryRun,
    [string]$Reason = 'owner stop'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')
if ($ProcessMatch) { $script:BACKEND_MATCH = $ProcessMatch }
if ($Port -lt 1 -or $Port -gt 65535) { Stop-D128 $EXIT_BAD_ARG "-Port must be 1..65535 (got $Port)" }
if ($TimeoutSeconds -lt 0 -or $TimeoutSeconds -gt 3600) { Stop-D128 $EXIT_BAD_ARG "-TimeoutSeconds must be 0..3600" }
if (-not (Test-Path -LiteralPath $LiveRoot -PathType Container)) { Stop-D128 $EXIT_BAD_ARG "-LiveRoot not found: $LiveRoot" }

$roots = @(Get-BackendRoots)
$listeners = @(Get-ListenerPids $Port)
$rootIds = @($roots | ForEach-Object { [int]$_.ProcessId })
$extra = @($listeners | Where-Object { $rootIds -notcontains [int]$_ })

if ($DryRun) {
    Write-Output "DRY RUN: nothing is changed."
    Write-Output ("  backend trees: {0}" -f (($roots | ForEach-Object { "$($_.ProcessId) $($_.CommandLine)" }) -join ' | '))
    Write-Output ("  other listeners on port {0}: {1}" -f $Port, ($extra -join ','))
    if (-not $NoMarker) { Write-Output "  would write $(Get-StopMarker $LiveRoot)" }
    exit $EXIT_OK
}

if (-not $NoMarker) {
    Set-Content -LiteralPath (Get-StopMarker $LiveRoot) -Value "stopped by owner $((Get-Date).ToUniversalTime().ToString('s'))Z: $Reason" -Encoding ASCII
}
if ($roots.Count -eq 0 -and $extra.Count -eq 0) {
    Write-RunRecord $LiveRoot @{ event = 'stop'; result = 'nothing_running'; reason = $Reason }
    Stop-D128 $EXIT_OK "nothing to stop (no app.main:app process, nothing on port $Port)"
}

# One CTRL+BREAK per backend tree (each tree has its own console), sent to the tree's deepest process (the server).
$all = @(Get-BackendProcesses)
foreach ($r in $roots) {
    $leaf = [int]$r.ProcessId
    $frontier = @($leaf)
    while ($true) {
        $kids = @($all | Where-Object { $frontier -contains [int]$_.ParentProcessId } | ForEach-Object { [int]$_.ProcessId })
        if ($kids.Count -eq 0) { break }
        $leaf = $kids[0]; $frontier = $kids
    }
    $sent = Send-CtrlBreak $leaf
    Write-Output ("CTRL+BREAK to tree {0} (pid {1}): {2}" -f $r.ProcessId, $leaf, $(if ($sent) { 'sent' } else { 'not sent; will force after the wait' }))
}
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    if (@(Get-BackendProcesses).Count -eq 0 -and @(Get-ListenerPids $Port).Count -eq 0) { break }
    Start-Sleep -Seconds 2
}
$left = @(Get-BackendProcesses)
$forced = @()
foreach ($p in $left) {
    try { Stop-Process -Id ([int]$p.ProcessId) -Force -ErrorAction Stop; $forced += [int]$p.ProcessId } catch { }
}
foreach ($procId in @(Get-ListenerPids $Port)) {
    try { Stop-Process -Id ([int]$procId) -Force -ErrorAction Stop; $forced += [int]$procId } catch { }
}
Start-Sleep -Seconds 2
$still = @(Get-BackendProcesses).Count + @(Get-ListenerPids $Port).Count
Write-RunRecord $LiveRoot @{ event = 'stop'; result = $(if ($still -eq 0) { 'stopped' } else { 'still_running' });
                             roots = $rootIds; forced = $forced; reason = $Reason; marker = (-not $NoMarker) }
if ($still -ne 0) { Stop-D128 $EXIT_FAILED "a backend process or port $Port listener is still there after the forced stop" }
if ($forced.Count -gt 0) { Write-Output "forced stop of pid(s) $($forced -join ',') after $TimeoutSeconds s (no clean shutdown: check the ledger digest)" }
Stop-D128 $EXIT_OK "live backend stopped"

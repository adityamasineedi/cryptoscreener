<#
.SYNOPSIS
  Move live to another pinned commit (owner rulings D46, D53, D128a; proposal P12). The owner runs it.

.DESCRIPTION
  1. Checks the arguments; the new commit must exist and, unless -AllowNonFastForward, be a fast-forward of the
     commit live runs now (so a deploy never silently drops merged work).
  2. Stops the backend (stop_live.ps1 -NoMarker), checks out the commit in the detached deploy worktree, starts
     it (start_live.ps1) and waits for the health check (writer lock, ledger ready, one copy, pinned).
  3. Records {event: deploy, from, to} in data\live_runs.jsonl. On a failed health check it prints the rollback
     command; with -AutoRollback it runs it.

    powershell -ExecutionPolicy Bypass -File D:\cryptoscreener-deploy\scripts\live\deploy_live.ps1 -Commit <sha or tag>

  Exit codes: 0 deployed and healthy, 2 bad argument, 3 refused, 4 failed.
#>
param(
    [string]$ProcessMatch = '',
    [string]$Commit = '',
    [switch]$AllowNonFastForward,
    [switch]$AutoRollback,
    [switch]$DryRun,
    [string]$LiveRoot = 'D:\cryptoscreener',
    [string]$DeployDir = 'D:\cryptoscreener-deploy',
    [string]$Python = '',
    [string]$EnvFile = '',
    [int]$Port = 8000,
    [string]$BindHost = '0.0.0.0',
    [int]$WaitSeconds = 300,
    [string]$EventName = 'deploy'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')
if ($ProcessMatch) { $script:BACKEND_MATCH = $ProcessMatch }
if (-not $Python) { $Python = Join-Path $LiveRoot 'backend\.venv\Scripts\python.exe' }
if (-not $EnvFile) { $EnvFile = Join-Path $LiveRoot '.env' }
$LiveRoot = Get-FullPath $LiveRoot
$DeployDir = Get-FullPath $DeployDir

if (-not $Commit) { Stop-D128 $EXIT_BAD_ARG "-Commit <sha or tag> is required" }
if (-not (Test-CommitSyntax $Commit)) { Stop-D128 $EXIT_BAD_ARG "-Commit is not a commit sha or tag name: '$Commit'" }
if ($WaitSeconds -lt 30 -or $WaitSeconds -gt 3600) { Stop-D128 $EXIT_BAD_ARG "-WaitSeconds must be 30..3600" }
Test-CommonArgs -LiveRoot $LiveRoot -DeployDir $DeployDir -Python $Python -Port $Port -EnvFile $EnvFile

$new = Resolve-Commit $LiveRoot $Commit
if (-not $new) { Stop-D128 $EXIT_BAD_ARG "-Commit '$Commit' is not a commit in $LiveRoot" }
$old = Get-DeployHead $DeployDir
if (-not $old) { Stop-D128 $EXIT_REFUSED "no deploy worktree at $DeployDir; the first start is start_live.ps1 -Commit $Commit" }
if (-not (Test-DeployClean $DeployDir)) { Stop-D128 $EXIT_REFUSED "$DeployDir has tracked changes; refusing to deploy over them" }
if ($new -eq $old) { Stop-D128 $EXIT_OK "already pinned at $new; nothing to deploy (start_live.ps1 starts it if it is down)" }
$ff = Invoke-Git $LiveRoot @('merge-base', '--is-ancestor', $old, $new)
if ($ff.code -ne 0 -and -not $AllowNonFastForward) {
    Stop-D128 $EXIT_REFUSED "$new is not a fast-forward of the running $old. Use rollback_live.ps1 to go back, or -AllowNonFastForward if you mean it"
}

$rollbackCmd = "powershell -ExecutionPolicy Bypass -File `"$(Join-Path $DeployDir 'scripts\live\rollback_live.ps1')`" -To $old"
if ($DryRun) {
    Write-Output "DRY RUN: nothing is changed. Plan:"
    Write-Output "  stop_live.ps1 -NoMarker -Port $Port"
    Write-Output "  git -C `"$DeployDir`" checkout --detach $new   (from $old)"
    Write-Output "  start_live.ps1 -Port $Port -WaitSeconds $WaitSeconds"
    Write-Output "  record {event: $EventName, from: $old, to: $new}"
    Write-Output "  if unhealthy: $rollbackCmd"
    exit $EXIT_OK
}

# Child scripts get the same process pattern, so a deploy only ever stops the copy this deploy is about.
$match = @{}
if ($ProcessMatch) { $match['ProcessMatch'] = $ProcessMatch }
& (Join-Path $PSScriptRoot 'stop_live.ps1') -Port $Port -LiveRoot $LiveRoot -NoMarker -Reason "$EventName to $new" @match
if ($LASTEXITCODE -ne 0) { Stop-D128 $EXIT_FAILED "stop failed; nothing deployed" }
& git -C $DeployDir checkout --quiet --detach $new
if ($LASTEXITCODE -ne 0) { Stop-D128 $EXIT_FAILED "git checkout $new failed in $DeployDir; live is STOPPED at $old. Start it again: start_live.ps1" }
Write-RunRecord $LiveRoot @{ event = $EventName; from = $old; to = $new }
# start_live.ps1 lives in the newly checked-out commit too; run this copy so the deploy logic stays the one you ran.
& (Join-Path $PSScriptRoot 'start_live.ps1') -LiveRoot $LiveRoot -DeployDir $DeployDir -Python $Python -EnvFile $EnvFile `
    -Port $Port -BindHost $BindHost -WaitSeconds $WaitSeconds @match
if ($LASTEXITCODE -ne 0) {
    if ($AutoRollback) {
        Write-Output "health check failed; rolling back to $old"
        & (Join-Path $PSScriptRoot 'deploy_live.ps1') -Commit $old -AllowNonFastForward -LiveRoot $LiveRoot -DeployDir $DeployDir `
            -Python $Python -EnvFile $EnvFile -Port $Port -BindHost $BindHost -WaitSeconds $WaitSeconds -EventName 'rollback' @match
        Stop-D128 $EXIT_FAILED "deploy of $new failed its health check; rolled back to $old (exit $LASTEXITCODE)"
    }
    Stop-D128 $EXIT_FAILED "deploy of $new failed its health check. Roll back with: $rollbackCmd"
}
Stop-D128 $EXIT_OK "deployed $new (was $old); healthy"

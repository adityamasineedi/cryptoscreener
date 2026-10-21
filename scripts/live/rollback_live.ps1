<#
.SYNOPSIS
  Put live back on the previous pinned commit (owner rulings D46, D53, D128a; proposal P12). The owner runs it.

.DESCRIPTION
  Without -To it takes the "from" commit of the last deploy or rollback that moved live to the commit it runs now
  (data\live_runs.jsonl). It then runs deploy_live.ps1 -AllowNonFastForward with event "rollback": stop, check
  out, start, health check.

    powershell -ExecutionPolicy Bypass -File D:\cryptoscreener-deploy\scripts\live\rollback_live.ps1
    powershell -ExecutionPolicy Bypass -File D:\cryptoscreener-deploy\scripts\live\rollback_live.ps1 -To <sha or tag>

  Exit codes: 0 rolled back and healthy, 2 bad argument, 3 refused (no history), 4 failed.
#>
param(
    [string]$ProcessMatch = '',
    [string]$BindHost = '0.0.0.0',
    [string]$To = '',
    [switch]$DryRun,
    [string]$LiveRoot = 'D:\cryptoscreener',
    [string]$DeployDir = 'D:\cryptoscreener-deploy',
    [string]$Python = '',
    [string]$EnvFile = '',
    [int]$Port = 8000,
    [int]$WaitSeconds = 300
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')
if ($To -and -not (Test-CommitSyntax $To)) { Stop-D128 $EXIT_BAD_ARG "-To is not a commit sha or tag name: '$To'" }
if (-not (Test-Path -LiteralPath $LiveRoot -PathType Container)) { Stop-D128 $EXIT_BAD_ARG "-LiveRoot not found: $LiveRoot" }

if (-not $To) {
    $head = Get-DeployHead $DeployDir
    if (-not $head) { Stop-D128 $EXIT_REFUSED "no deploy worktree at $DeployDir; nothing to roll back" }
    $moves = @(Read-RunRecords $LiveRoot | Where-Object {
        $_.PSObject.Properties['event'] -and ($_.event -eq 'deploy' -or $_.event -eq 'rollback') -and
        $_.PSObject.Properties['to'] -and $_.to -eq $head -and $_.PSObject.Properties['from'] })
    if ($moves.Count -eq 0) { Stop-D128 $EXIT_REFUSED "no recorded deploy to $head in $(Get-RunsFile $LiveRoot); pass -To <sha or tag>" }
    $To = $moves[$moves.Count - 1].from
    Write-Output "rolling back from $head to $To (the commit before the last deploy)"
}

$fwd = @{ Commit = $To; AllowNonFastForward = $true; LiveRoot = $LiveRoot; DeployDir = $DeployDir; Port = $Port; BindHost = $BindHost;
          WaitSeconds = $WaitSeconds; EventName = 'rollback' }
if ($Python) { $fwd['Python'] = $Python }
if ($EnvFile) { $fwd['EnvFile'] = $EnvFile }
if ($DryRun) { $fwd['DryRun'] = $true }
if ($ProcessMatch) { $fwd['ProcessMatch'] = $ProcessMatch }
& (Join-Path $PSScriptRoot 'deploy_live.ps1') @fwd
exit $LASTEXITCODE

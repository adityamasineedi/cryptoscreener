<#
.SYNOPSIS
  Start the live backend once, without --reload, from a pinned commit (owner rulings D46, D128a; proposal P12).

.DESCRIPTION
  The code runs from a separate, detached deploy worktree (default D:\cryptoscreener-deploy). Saving files in
  D:\cryptoscreener no longer restarts or changes live. Live state stays where it is: the deploy worktree's
  data\ and backend\data\ are NTFS junctions into the live root, and settings come from the live root's .env
  (passed to uvicorn --env-file; this script never reads it).

  First start (creates the deploy worktree at the commit):
    powershell -ExecutionPolicy Bypass -File D:\cryptoscreener\scripts\live\start_live.ps1 -Commit <sha or tag>
  Later starts (whatever is deployed):
    powershell -ExecutionPolicy Bypass -File D:\cryptoscreener-deploy\scripts\live\start_live.ps1
  Auto-start task (register_autostart.ps1) calls it with -Auto: it exits quietly when live is already up, when
  the owner stopped live on purpose (data\live_stopped_by_owner), or after 3 auto-starts in 60 minutes (it then
  writes data\live_autostart_paused and stops trying until the owner deletes that file).

  Refuses (exit 3) when anything already listens on the port or any app.main:app process exists, when the
  deploy worktree has tracked changes, or when data\ in the deploy worktree is a real folder instead of a link.
  -DryRun prints the plan and changes nothing.

  Exit codes: 0 ok, 2 bad argument, 3 refused, 4 failed.
#>
param(
    [string]$ProcessMatch = '',
    [string]$Commit = '',
    [switch]$Auto,
    [switch]$DryRun,
    [string]$LiveRoot = 'D:\cryptoscreener',
    [string]$DeployDir = 'D:\cryptoscreener-deploy',
    [string]$Python = '',
    [string]$EnvFile = '',
    [int]$Port = 8000,
    [string]$BindHost = '0.0.0.0',
    [string]$LogDir = '',
    [int]$WaitSeconds = -1,
    [int]$MaxAutoStartsPerHour = 3
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')
if ($ProcessMatch) { $script:BACKEND_MATCH = $ProcessMatch }

if (-not $Python) { $Python = Join-Path $LiveRoot 'backend\.venv\Scripts\python.exe' }
if (-not $EnvFile) { $EnvFile = Join-Path $LiveRoot '.env' }
if (-not $LogDir) { $LogDir = Join-Path $env:LOCALAPPDATA 'cryptoscreener\logs' }
if ($WaitSeconds -lt 0) { if ($Auto) { $WaitSeconds = 0 } else { $WaitSeconds = 180 } }
$LiveRoot = Get-FullPath $LiveRoot
$DeployDir = Get-FullPath $DeployDir

if ($Commit -and -not (Test-CommitSyntax $Commit)) { Stop-D128 $EXIT_BAD_ARG "-Commit is not a commit sha or tag name: '$Commit'" }
if ($Auto -and $Commit) { Stop-D128 $EXIT_BAD_ARG "-Auto starts what is deployed; it takes no -Commit (use deploy_live.ps1 to change it)" }
Test-CommonArgs -LiveRoot $LiveRoot -DeployDir $DeployDir -Python $Python -Port $Port -EnvFile $EnvFile

$wanted = $null
if ($Commit) {
    $wanted = Resolve-Commit $LiveRoot $Commit
    if (-not $wanted) { Stop-D128 $EXIT_BAD_ARG "-Commit '$Commit' is not a commit in $LiveRoot" }
}

$mode = 'manual'
if ($Auto) { $mode = 'auto' }

# --- already running? -----------------------------------------------------------------------------------
$listeners = @(Get-ListenerPids $Port)
$backends = @(Get-BackendProcesses)
if ($Auto) {
    if (Test-Path -LiteralPath (Get-StopMarker $LiveRoot)) { Stop-D128 $EXIT_OK "auto: live was stopped by the owner ($(Get-StopMarker $LiveRoot)); not starting" }
    if (Test-Path -LiteralPath (Get-PauseMarker $LiveRoot)) { Stop-D128 $EXIT_OK "auto: auto-start is paused ($(Get-PauseMarker $LiveRoot)); not starting" }
    if ($listeners.Count -gt 0 -or $backends.Count -gt 0) { Stop-D128 $EXIT_OK "auto: live already running (port $Port listeners: $($listeners -join ','); backend pids: $(($backends | ForEach-Object { $_.ProcessId }) -join ','))" }
    $since = (Get-Date).ToUniversalTime().AddMinutes(-60)
    $recent = @(Read-RunRecords $LiveRoot | Where-Object {
        $_.PSObject.Properties['event'] -and $_.event -eq 'start' -and $_.PSObject.Properties['mode'] -and $_.mode -eq 'auto' -and
        $_.PSObject.Properties['at_utc'] -and ((ConvertTo-UtcDate $_.at_utc) -ge $since) })
    if ($recent.Count -ge $MaxAutoStartsPerHour) {
        if (-not $DryRun) {
            Set-Content -LiteralPath (Get-PauseMarker $LiveRoot) -Value "paused $((Get-Date).ToUniversalTime().ToString('s'))Z after $($recent.Count) auto-starts in 60 min" -Encoding ASCII
            Write-RunRecord $LiveRoot @{ event = 'autostart_paused'; mode = 'auto'; starts_last_hour = $recent.Count }
        }
        Stop-D128 $EXIT_REFUSED "auto: $($recent.Count) auto-starts in the last 60 minutes; crash loop suspected. Paused. Read the backend logs in $LogDir, fix, then delete $(Get-PauseMarker $LiveRoot)"
    }
} else {
    if ($listeners.Count -gt 0) { Stop-D128 $EXIT_REFUSED "port $Port is already in use by pid(s) $($listeners -join ','); one live copy only. Use stop_live.ps1 or deploy_live.ps1" }
    if ($backends.Count -gt 0) { Stop-D128 $EXIT_REFUSED "an app.main:app process already runs (pids $(($backends | ForEach-Object { $_.ProcessId }) -join ',')); one live copy only" }
}

# --- deploy worktree ------------------------------------------------------------------------------------
$plan = @()
$head = Get-DeployHead $DeployDir
if (-not $head) {
    if (Test-Path -LiteralPath $DeployDir) { Stop-D128 $EXIT_REFUSED "$DeployDir exists but is not a git worktree; move it away first" }
    if (-not $wanted) { Stop-D128 $EXIT_BAD_ARG "no deploy worktree at $DeployDir yet: the first start needs -Commit <sha or tag>" }
    $plan += "git -C `"$LiveRoot`" worktree add --detach `"$DeployDir`" $wanted"
    $head = $wanted
} else {
    if (-not (Test-SameRepository $DeployDir $LiveRoot)) { Stop-D128 $EXIT_REFUSED "$DeployDir is a worktree of another repository" }
    if (-not (Test-DeployClean $DeployDir)) { Stop-D128 $EXIT_REFUSED "$DeployDir has tracked changes; the pinned code must equal its commit (git -C `"$DeployDir`" status)" }
    if ($wanted -and $wanted -ne $head) {
        Stop-D128 $EXIT_REFUSED "deploy worktree is at $head, not $wanted. Change the pinned commit with deploy_live.ps1 -Commit $Commit"
    }
    $branch = Invoke-Git $DeployDir @('symbolic-ref', '-q', 'HEAD')
    if ($branch.code -eq 0) { Stop-D128 $EXIT_REFUSED "$DeployDir is on branch $($branch.out); the deploy worktree must be a detached checkout (git -C `"$DeployDir`" checkout --detach $head)" }
}

foreach ($rel in $STATE_LINKS) {
    $link = Join-Path $DeployDir $rel
    $target = Join-Path $LiveRoot $rel
    if (-not (Test-Path -LiteralPath $target -PathType Container)) {
        Stop-D128 $EXIT_REFUSED "live state folder $target does not exist; it must be there before live starts from a deploy worktree"
    }
    $now = Get-JunctionTarget $link
    if ($null -eq $now) {
        $plan += "New-Item -ItemType Junction -Path `"$link`" -Target `"$target`""
    } elseif ($now -eq '') {
        Stop-D128 $EXIT_REFUSED "$link is a real folder, not a link to $target. Live state would split; move its contents into $target and delete it"
    } elseif ((Get-FullPath $now).ToLowerInvariant() -ne (Get-FullPath $target).ToLowerInvariant()) {
        Stop-D128 $EXIT_REFUSED "$link points to $now, not $target"
    }
}

$stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')
$outLog = Join-Path $LogDir "backend-$stamp.out.log"
$errLog = Join-Path $LogDir "backend-$stamp.err.log"
$uv = Get-UvicornArgs $DeployDir $EnvFile $BindHost $Port
$plan += "cd `"$(Join-Path $DeployDir 'backend')`"; `"$Python`" $($uv -join ' ')  (hidden; logs $outLog, $errLog)"
$plan += "append {event: start, mode: $mode, commit: $head} to $(Get-RunsFile $LiveRoot)"

if ($DryRun) {
    Write-Output "DRY RUN ($mode): nothing is changed. Plan:"
    $plan | ForEach-Object { Write-Output "  $_" }
    exit $EXIT_OK
}

# --- act ------------------------------------------------------------------------------------------------
if (-not (Get-DeployHead $DeployDir)) {
    & git -C $LiveRoot worktree add --detach $DeployDir $wanted
    if ($LASTEXITCODE -ne 0) { Stop-D128 $EXIT_FAILED "git worktree add failed" }
}
foreach ($rel in $STATE_LINKS) {
    $link = Join-Path $DeployDir $rel
    if ($null -eq (Get-JunctionTarget $link)) {
        $parent = Split-Path -Parent $link
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }
        New-Item -ItemType Junction -Path $link -Target (Join-Path $LiveRoot $rel) | Out-Null
    }
}
if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
if (-not $Auto -and (Test-Path -LiteralPath (Get-StopMarker $LiveRoot))) { Remove-Item -LiteralPath (Get-StopMarker $LiveRoot) }

$launcherPid = Start-Detached -Exe $Python -Arguments $uv -WorkingDirectory (Join-Path $DeployDir 'backend') -OutLog $outLog -ErrLog $errLog
Write-RunRecord $LiveRoot @{ event = 'start'; mode = $mode; commit = $head; deploy_dir = $DeployDir; launcher_pid = $launcherPid; port = $Port;
                             python = $Python; log_out = $outLog; log_err = $errLog }
Write-Output "started live backend (launcher pid $launcherPid) at commit $head (mode $mode); logs $outLog"

if ($WaitSeconds -gt 0) {
    $match = @{}
    if ($ProcessMatch) { $match['ProcessMatch'] = $ProcessMatch }
    & (Join-Path $PSScriptRoot 'health_check.ps1') -Port $Port -LiveRoot $LiveRoot -DeployDir $DeployDir -WaitSeconds $WaitSeconds @match
    if ($LASTEXITCODE -ne 0) { Stop-D128 $EXIT_FAILED "started, but the health check did not pass within $WaitSeconds s; see $errLog" }
}
exit $EXIT_OK

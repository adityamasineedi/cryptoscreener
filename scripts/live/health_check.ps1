<#
.SYNOPSIS
  Read-only health check of the live backend (owner rulings D46, D128a; proposal P12).

.DESCRIPTION
  Checks, in order:
    api_health      GET /api/health answers 200 with status ok
    database        /api/health reports database ok
    writer_lock     /api/paper/status writer_lock_held is true (this process is the one paper writer)
    ledger_ready    /api/paper/status ledger_ready is true
    one_listener    exactly one process listens on the port
    one_backend     exactly one app.main:app process tree
    no_reload       that process was started without --reload
    pinned          it runs from the deploy worktree (--app-dir <DeployDir>\backend) at the last recorded commit,
                    and the deploy worktree has no tracked changes
    journal_fresh   data\paper\decision_journal.jsonl written in the last 70 minutes (WARN only: closed 1h bars)
  -WaitSeconds N re-checks every 5 s until every check passes or N seconds have gone by.
  -Json prints one JSON object instead of the table.
  Exit codes: 0 all checks pass (WARN allowed), 1 at least one FAIL, 2 bad argument.

    powershell -ExecutionPolicy Bypass -File D:\cryptoscreener-deploy\scripts\live\health_check.ps1
#>
param(
    [string]$ProcessMatch = '',
    [int]$Port = 8000,
    [string]$LiveRoot = 'D:\cryptoscreener',
    [string]$DeployDir = 'D:\cryptoscreener-deploy',
    [int]$WaitSeconds = 0,
    [int]$TimeoutSeconds = 10,
    [switch]$Json
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')
if ($ProcessMatch) { $script:BACKEND_MATCH = $ProcessMatch }
if ($Port -lt 1 -or $Port -gt 65535) { Stop-D128 $EXIT_BAD_ARG "-Port must be 1..65535 (got $Port)" }
if ($WaitSeconds -lt 0) { Stop-D128 $EXIT_BAD_ARG "-WaitSeconds must be >= 0" }

function Get-Api([string]$path) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec $TimeoutSeconds "http://127.0.0.1:$Port$path"
        return @{ ok = ($r.StatusCode -eq 200); body = ($r.Content | ConvertFrom-Json) }
    } catch {
        return @{ ok = $false; body = $null; error = $_.Exception.Message }
    }
}

function Get-Prop($obj, [string]$name) {
    if ($null -eq $obj) { return $null }
    $p = $obj.PSObject.Properties[$name]
    if ($p) { return $p.Value }
    return $null
}

function Invoke-Checks {
    $rows = New-Object System.Collections.ArrayList
    $add = { param($name, $status, $detail) [void]$rows.Add([pscustomobject]@{ check = $name; status = $status; detail = $detail }) }

    $h = Get-Api '/api/health'
    if ($h.ok -and (Get-Prop $h.body 'status') -eq 'ok') { & $add 'api_health' 'OK' "port $Port" }
    else { & $add 'api_health' 'FAIL' ("no healthy answer on port {0}: {1}" -f $Port, $h['error']) }
    $db = Get-Prop $h.body 'database'
    if ($db -eq 'ok') { & $add 'database' 'OK' 'database ok' } else { & $add 'database' 'FAIL' "database: $db" }

    $s = Get-Api '/api/paper/status'
    $lock = Get-Prop $s.body 'writer_lock_held'
    $ready = Get-Prop $s.body 'ledger_ready'
    if ($lock -eq $true) { & $add 'writer_lock' 'OK' 'writer_lock_held true' } else { & $add 'writer_lock' 'FAIL' "writer_lock_held: $lock" }
    if ($ready -eq $true) { & $add 'ledger_ready' 'OK' 'ledger_ready true' } else { & $add 'ledger_ready' 'FAIL' "ledger_ready: $ready" }

    $listeners = @(Get-ListenerPids $Port)
    if ($listeners.Count -eq 1) { & $add 'one_listener' 'OK' "pid $($listeners[0])" }
    else { & $add 'one_listener' 'FAIL' ("{0} listeners on port {1}: {2}" -f $listeners.Count, $Port, ($listeners -join ',')) }

    $roots = @(Get-BackendRoots)
    if ($roots.Count -eq 1) { & $add 'one_backend' 'OK' "root pid $($roots[0].ProcessId)" }
    else { & $add 'one_backend' 'FAIL' ("{0} app.main:app trees: {1}" -f $roots.Count, (($roots | ForEach-Object { $_.ProcessId }) -join ',')) }

    $cmd = ''
    if ($roots.Count -ge 1) { $cmd = [string]$roots[0].CommandLine }
    if ($roots.Count -ge 1 -and $cmd -notmatch '--reload') { & $add 'no_reload' 'OK' 'started without --reload' }
    else { & $add 'no_reload' 'FAIL' 'running with --reload or not running' }

    $head = Get-DeployHead $DeployDir
    $runs = @(Read-RunRecords $LiveRoot | Where-Object { $_.PSObject.Properties['commit'] -and $_.PSObject.Properties['event'] -and $_.event -eq 'start' })
    $recorded = $null
    if ($runs.Count -gt 0) { $recorded = $runs[$runs.Count - 1].commit }
    $appDir = (Join-Path $DeployDir 'backend').ToLowerInvariant()
    $why = @()
    if (-not $head) { $why += "no deploy worktree at $DeployDir" }
    elseif (-not (Test-DeployClean $DeployDir)) { $why += 'deploy worktree has tracked changes' }
    if ($head -and $recorded -and $head -ne $recorded) { $why += "deploy HEAD $head is not the last started commit $recorded" }
    if ($cmd -and -not $cmd.ToLowerInvariant().Contains($appDir)) { $why += "process does not run from $appDir" }
    if ($why.Count -eq 0 -and $head) { & $add 'pinned' 'OK' "commit $head" } else { & $add 'pinned' 'FAIL' ($why -join '; ') }

    $journal = Join-Path $LiveRoot 'data\paper\decision_journal.jsonl'
    if (Test-Path -LiteralPath $journal) {
        $age = ((Get-Date) - (Get-Item -LiteralPath $journal).LastWriteTime).TotalMinutes
        if ($age -le 70) { & $add 'journal_fresh' 'OK' ("written {0:N0} min ago" -f $age) }
        else { & $add 'journal_fresh' 'WARN' ("last write {0:N0} min ago (closed 1h bars: expect under 70)" -f $age) }
    } else { & $add 'journal_fresh' 'WARN' "no journal at $journal" }
    return ,$rows
}

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ($true) {
    $rows = Invoke-Checks
    $failed = @($rows | Where-Object { $_.status -eq 'FAIL' })
    if ($failed.Count -eq 0 -or (Get-Date) -ge $deadline) { break }
    Start-Sleep -Seconds 5
}
if ($Json) {
    $verdict = 'OK'
    if ($failed.Count -gt 0) { $verdict = 'FAIL' }
    [pscustomobject]@{ verdict = $verdict; at_utc = (Get-Date).ToUniversalTime().ToString('s') + 'Z'; checks = $rows } | ConvertTo-Json -Depth 4
} else {
    $rows | Format-Table -AutoSize | Out-String -Width 200 | Write-Output
    if ($failed.Count -eq 0) { Write-Output 'HEALTH OK' } else { Write-Output "HEALTH FAIL: $(($failed | ForEach-Object { $_.check }) -join ', ')" }
}
if ($failed.Count -eq 0) { exit 0 }
exit 1

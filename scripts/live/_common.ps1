# Shared helpers for the P12 live scripts (owner rulings D46, D128a). Dot-sourced; never run directly.
# Windows PowerShell 5.1 compatible. The scripts never read .env contents: they only check that the file exists
# and pass its path to uvicorn --env-file.
#
# Exit codes used by every script:
#   0 ok    2 bad argument    3 refused by a safety check    4 runtime failure (health, git, start)

Set-StrictMode -Version 2.0

$script:EXIT_OK = 0
$script:EXIT_BAD_ARG = 2
$script:EXIT_REFUSED = 3
$script:EXIT_FAILED = 4

# Relative paths that hold live state. In the deploy worktree they are NTFS junctions into the live root,
# so the pinned code reads and writes the same journal, watermarks, stop counters and virtual stores.
$script:STATE_LINKS = @('data', 'backend\data')

function Stop-D128([int]$code, [string]$message) {
    if ($code -eq 0) { Write-Output $message } else { [Console]::Error.WriteLine("ERROR: $message") }
    exit $code
}

function Get-FullPath([string]$path) {
    return [System.IO.Path]::GetFullPath($path).TrimEnd('\')
}

function Test-InsideOrEqual([string]$child, [string]$parent) {
    $c = (Get-FullPath $child).ToLowerInvariant()
    $p = (Get-FullPath $parent).ToLowerInvariant()
    return ($c -eq $p) -or $c.StartsWith($p + '\')
}

function Test-CommitSyntax([string]$commit) {
    # A hex sha (7-40) or a tag/branch-like name. No spaces, no shell metacharacters, no '..', no leading '-'.
    if ([string]::IsNullOrWhiteSpace($commit)) { return $false }
    if ($commit -match '^[0-9a-fA-F]{7,40}$') { return $true }
    if ($commit.StartsWith('-') -or $commit.Contains('..')) { return $false }
    return ($commit -match '^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$')
}

function Invoke-Git([string]$repo, [string[]]$gitArgs) {
    $out = & git -C $repo @gitArgs 2>$null
    return @{ code = $LASTEXITCODE; out = (($out | Out-String).Trim()) }
}

function Resolve-Commit([string]$repo, [string]$commit) {
    # Full sha of a commit, or $null. --verify --quiet: no output on failure.
    $r = Invoke-Git $repo @('rev-parse', '--verify', '--quiet', "$commit^{commit}")
    if ($r.code -ne 0 -or -not $r.out) { return $null }
    return $r.out
}

function Test-CommonArgs {
    param([string]$LiveRoot, [string]$DeployDir, [string]$Python, [int]$Port, [string]$EnvFile)
    if ($Port -lt 1 -or $Port -gt 65535) { Stop-D128 $EXIT_BAD_ARG "-Port must be 1..65535 (got $Port)" }
    if (-not (Test-Path -LiteralPath $LiveRoot -PathType Container)) { Stop-D128 $EXIT_BAD_ARG "-LiveRoot not found: $LiveRoot" }
    $top = Invoke-Git $LiveRoot @('rev-parse', '--show-toplevel')
    if ($top.code -ne 0) { Stop-D128 $EXIT_BAD_ARG "-LiveRoot is not a git checkout: $LiveRoot" }
    if (Test-InsideOrEqual $DeployDir $LiveRoot) {
        Stop-D128 $EXIT_BAD_ARG "-DeployDir must be outside -LiveRoot (got $DeployDir); editing the live checkout must never reach the running code"
    }
    if (Test-InsideOrEqual $LiveRoot $DeployDir) { Stop-D128 $EXIT_BAD_ARG "-LiveRoot must not be inside -DeployDir" }
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { Stop-D128 $EXIT_BAD_ARG "-Python not found: $Python" }
    if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) { Stop-D128 $EXIT_BAD_ARG "-EnvFile not found: $EnvFile (the scripts never read it; uvicorn loads it)" }
}

function Get-ListenerPids([int]$port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $c) { return @() }
    return @($c | Select-Object -ExpandProperty OwningProcess -Unique)
}

# Command-line pattern of a backend process. Tests point it at a pattern that matches nothing.
$script:BACKEND_MATCH = 'app\.main:app'

function Get-BackendProcesses {
    # Every python/uvicorn process that runs the app (reloader or server), plus spawn children of those.
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $roots = @($all | Where-Object { $_.Name -match '^(python|pythonw|uvicorn)' -and $_.CommandLine -and $_.CommandLine -match $script:BACKEND_MATCH })
    $ids = @{}
    foreach ($p in $roots) { $ids[[int]$p.ProcessId] = $true }
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($p in $all) {
            if (-not $ids.ContainsKey([int]$p.ProcessId) -and $ids.ContainsKey([int]$p.ParentProcessId)) {
                $ids[[int]$p.ProcessId] = $true; $changed = $true
            }
        }
    }
    return @($all | Where-Object { $ids.ContainsKey([int]$_.ProcessId) })
}

function Get-BackendRoots {
    $procs = @(Get-BackendProcesses)
    $ids = @{}
    foreach ($p in $procs) { $ids[[int]$p.ProcessId] = $true }
    return @($procs | Where-Object { -not $ids.ContainsKey([int]$_.ParentProcessId) })
}

function Get-RunsFile([string]$LiveRoot) { return (Join-Path $LiveRoot 'data\live_runs.jsonl') }
function Get-StopMarker([string]$LiveRoot) { return (Join-Path $LiveRoot 'data\live_stopped_by_owner') }
function Get-PauseMarker([string]$LiveRoot) { return (Join-Path $LiveRoot 'data\live_autostart_paused') }

function Write-RunRecord([string]$LiveRoot, [hashtable]$row) {
    $row['at_utc'] = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $file = Get-RunsFile $LiveRoot
    $dir = Split-Path -Parent $file
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    $line = (New-Object PSObject -Property $row | ConvertTo-Json -Compress)
    [System.IO.File]::AppendAllText($file, $line + "`n", (New-Object System.Text.UTF8Encoding($false)))
}

function Read-RunRecords([string]$LiveRoot) {
    $file = Get-RunsFile $LiveRoot
    if (-not (Test-Path -LiteralPath $file)) { return @() }
    $rows = @()
    foreach ($line in [System.IO.File]::ReadAllLines($file)) {
        if ($line.Trim()) { try { $rows += ($line | ConvertFrom-Json) } catch { } }
    }
    return $rows
}

function ConvertTo-UtcDate($value) {
    if ($value -is [datetime]) { return $value.ToUniversalTime() }
    $styles = [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
    return [datetime]::Parse([string]$value, [Globalization.CultureInfo]::InvariantCulture, $styles)
}

function Get-DeployHead([string]$DeployDir) {
    if (-not (Test-Path -LiteralPath (Join-Path $DeployDir '.git'))) { return $null }
    $r = Invoke-Git $DeployDir @('rev-parse', 'HEAD')
    if ($r.code -ne 0) { return $null }
    return $r.out
}

function Test-DeployClean([string]$DeployDir) {
    # Tracked files only: data\ and backend\data\ are junctions full of untracked state.
    $r = Invoke-Git $DeployDir @('status', '--porcelain', '--untracked-files=no')
    return ($r.code -eq 0 -and -not $r.out)
}

function Test-SameRepository([string]$a, [string]$b) {
    $ra = Invoke-Git $a @('rev-parse', '--path-format=absolute', '--git-common-dir')
    $rb = Invoke-Git $b @('rev-parse', '--path-format=absolute', '--git-common-dir')
    if ($ra.code -ne 0 -or $rb.code -ne 0) { return $false }
    return ((Get-FullPath $ra.out).ToLowerInvariant() -eq (Get-FullPath $rb.out).ToLowerInvariant())
}

function Get-JunctionTarget([string]$path) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if (-not $item) { return $null }
    if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return '' }  # a real directory
    $t = $item.Target
    if ($t -is [array]) { $t = $t[0] }
    return [string]$t
}

function Get-UvicornArgs([string]$DeployDir, [string]$EnvFile, [string]$BindHost, [int]$Port) {
    # No --reload, one process. --app-dir puts the pinned backend first on sys.path.
    return @('-m', 'uvicorn', 'app.main:app', '--host', $BindHost, '--port', "$Port",
             '--env-file', $EnvFile, '--app-dir', (Join-Path $DeployDir 'backend'))
}

function ConvertTo-CmdArg([string]$a) {
    if ($a -match '[\s"&|<>^]') { return '"' + ($a -replace '"', '\"') + '"' }
    return $a
}

function Start-Detached {
    # Start the backend through WMI (Win32_Process.Create): its own hidden console, no handles inherited from this
    # PowerShell (a caller that captures our output never waits on the server), and outside the caller's job
    # object (Task Scheduler ending the task does not end the server). cmd.exe only redirects stdout/stderr.
    param([string]$Exe, [string[]]$Arguments, [string]$WorkingDirectory, [string]$OutLog, [string]$ErrLog)
    $inner = (@($Exe) + $Arguments | ForEach-Object { ConvertTo-CmdArg $_ }) -join ' '
    $line = 'cmd.exe /d /c "' + $inner + ' > "' + $OutLog + '" 2> "' + $ErrLog + '""'
    $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = $line; CurrentDirectory = $WorkingDirectory; ProcessStartupInformation = $startup }
    if ($r.ReturnValue -ne 0) { Stop-D128 $EXIT_FAILED "Win32_Process.Create failed (code $($r.ReturnValue)) for: $line" }
    return [int]$r.ProcessId
}

function Send-CtrlBreak([int]$procId) {
    # Graceful stop: send_ctrl_break.ps1 attaches to the target's (hidden) console and sends CTRL+BREAK. uvicorn
    # handles SIGBREAK like SIGINT (clean lifespan shutdown). CTRL+C would be ignored: Win32_Process.Create starts
    # the server in its own process group. Returns $true when the event was sent.
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'send_ctrl_break.ps1') -TargetPid $procId | Out-Null
    return ($LASTEXITCODE -eq 0)
}

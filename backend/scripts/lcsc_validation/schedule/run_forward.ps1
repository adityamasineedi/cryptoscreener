<#
LCSC forward runner wrapper for Windows Task Scheduler (hourly at minute 20 UTC).

Runs `forward.py run`. On any non-zero exit it appends a line to forward_out\ALERTS.log
and writes the full output to forward_out\last_failure.log, then exits with the same
code so Task Scheduler's "Last Run Result" shows the failure.

Exit codes from forward.py: 0 ok, 2 refused, 3 CONFIG_DRIFT, 4 RECORD_INTEGRITY,
5 CONCURRENT_RUN. Anything else is a crash.
#>
$ErrorActionPreference = "Continue"
$backend = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$out = Join-Path $backend "scripts\lcsc_validation\forward_out"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$python = Join-Path $backend ".venv\Scripts\python.exe"
$env:PYTHONPATH = $backend
Set-Location $backend
$started = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$output = & $python "scripts\lcsc_validation\forward.py" run 2>&1 | Out-String
$code = $LASTEXITCODE
if ($code -ne 0) {
    $reason = switch ($code) { 2 {"REFUSED"} 3 {"CONFIG_DRIFT"} 4 {"RECORD_INTEGRITY"} 5 {"CONCURRENT_RUN"} default {"CRASH"} }
    Add-Content -Path (Join-Path $out "ALERTS.log") -Value "$started LCSC forward run failed: exit $code $reason"
    Set-Content -Path (Join-Path $out "last_failure.log") -Value $output
}
exit $code

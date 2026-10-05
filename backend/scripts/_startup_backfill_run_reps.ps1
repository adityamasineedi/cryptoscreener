# Full-restart validation loop for startup backfill burst control.
# Does not overwrite prior follow-up reports; writes to sandbox OUT dir.

$ErrorActionPreference = "Stop"
$Backend = "E:\cryptoscreener\backend"
$VenvPython = Join-Path $Backend ".venv\Scripts\python.exe"
$Uvicorn = Join-Path $Backend ".venv\Scripts\uvicorn.exe"
$Out = Join-Path $Backend "reports\startup_backfill_burst_control"
$Port = 8001
$Base = "http://127.0.0.1:$Port"
$Duration = if ($env:FOLLOWUP_STRESS_SECONDS) { $env:FOLLOWUP_STRESS_SECONDS } else { "600" }
$Reps = if ($env:STARTUP_BACKFILL_REPS) { [int]$env:STARTUP_BACKFILL_REPS } else { 3 }

New-Item -ItemType Directory -Force -Path $Out | Out-Null
$env:PYTHONPATH = $Backend
$env:FOLLOWUP_API_BASE = $Base
$env:STARTUP_BACKFILL_OUT = "reports/startup_backfill_burst_control"
$env:FOLLOWUP_STRESS_SECONDS = "$Duration"
$env:STARTUP_WINDOW_SECONDS = "60"
$env:ENABLE_MARKET_STRUCTURE_ANALYTICS = "false"

function Stop-PortListener([int]$PortNum) {
  $conns = Get-NetTCPConnection -LocalPort $PortNum -State Listen -ErrorAction SilentlyContinue
  foreach ($c in $conns) {
    try { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue } catch {}
  }
  Start-Sleep -Seconds 2
}

$allSummaries = @()
for ($rep = 1; $rep -le $Reps; $rep++) {
  Write-Host "==== REP $rep / $Reps full restart ====" -ForegroundColor Cyan
  Stop-PortListener $Port

  $stdout = Join-Path $Out "uvicorn_rep${rep}_stdout.txt"
  $stderr = Join-Path $Out "uvicorn_rep${rep}_stderr.txt"
  $proc = Start-Process -FilePath $Uvicorn `
    -ArgumentList @("app.main:app", "--host", "127.0.0.1", "--port", "$Port") `
    -WorkingDirectory $Backend `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru `
    -WindowStyle Hidden

  # Wait until health responds
  $ready = $false
  for ($i = 0; $i -lt 120; $i++) {
    try {
      $h = Invoke-RestMethod -Uri "$Base/api/health" -TimeoutSec 5
      if ($h.status -eq "ok") { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 1
  }
  if (-not $ready) {
    Write-Host "Health not ready for rep $rep" -ForegroundColor Red
    try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
    continue
  }

  $env:STARTUP_BACKFILL_REPS = "1"
  $env:STARTUP_BACKFILL_REP_TAG = "rep$rep"
  & $VenvPython (Join-Path $Backend "scripts\_startup_backfill_validation.py")
  if ($LASTEXITCODE -ne 0) {
    Write-Host "Validation script failed rep $rep exit=$LASTEXITCODE" -ForegroundColor Yellow
  }

  try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
  Stop-PortListener $Port
  Start-Sleep -Seconds 3
}

Write-Host "All reps finished. Artifacts in $Out"

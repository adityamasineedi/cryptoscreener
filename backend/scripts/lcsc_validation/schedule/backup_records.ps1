<#
Back up the gitignored LCSC record store (forward_out) and verify the copy.

  powershell -ExecutionPolicy Bypass -File backup_records.ps1 -Destination D:\backups\lcsc

Copies forward_out to <Destination>\forward_out_<UTC timestamp>, writes SHA256SUMS for the
copy, then runs `forward.py verify-records` on the copy (hash chain intact, no torn line).
Exit code 0 only if the copy verifies. Keep at least the last 7 daily copies off this disk.
#>
param([Parameter(Mandatory = $true)][string]$Destination)
$backend = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$src = Join-Path $backend "scripts\lcsc_validation\forward_out"
if (-not (Test-Path $src)) { Write-Error "no record store at $src"; exit 2 }
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$dst = Join-Path $Destination "forward_out_$stamp"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item -Path (Join-Path $src "*.jsonl") -Destination $dst
Get-ChildItem $dst -Filter *.jsonl | ForEach-Object {
    "{0} *{1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name
} | Set-Content -Path (Join-Path $dst "SHA256SUMS") -Encoding ascii
$env:PYTHONPATH = $backend
& (Join-Path $backend ".venv\Scripts\python.exe") (Join-Path $backend "scripts\lcsc_validation\forward.py") verify-records --path $dst
exit $LASTEXITCODE

<#
Registers the hourly LCSC forward run with Windows Task Scheduler for the current user.
Runs at minute 20 of every hour (local clock; at UTC+05:30 that is minute 50 UTC, still
inside the hour, after the bar closed and ingestion caught up). Review before running.

  powershell -ExecutionPolicy Bypass -File register_windows_task.ps1
  schtasks /Query /TN "LCSC forward run" /V /FO LIST      # check it
  schtasks /Delete /TN "LCSC forward run" /F              # remove it
#>
$wrapper = Join-Path $PSScriptRoot "run_forward.ps1"
$action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$wrapper`""
schtasks /Create /TN "LCSC forward run" /TR $action /SC HOURLY /MO 1 /ST 00:20 /F

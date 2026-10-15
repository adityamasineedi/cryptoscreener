# Operate the CANDLE12_V3 OOS-2 runner task.
#   control.ps1 status   task state, supervisor and runner processes, last heartbeat
#   control.ps1 start    enable the task and start it (the supervisor runs the runner with --recover)
#   control.ps1 stop     disable the task, then stop the supervisor and the runner. The runner has no
#                        graceful stop, so the next start takes over its run lock once it is stale
#                        (15 min) and records a RUNNER_CRASH exception, which must be resolved with a note.
#   control.ps1 restart  stop, then start (same 15-min takeover)
param([Parameter(Mandatory = $true)][ValidateSet("status", "start", "stop", "restart")][string]$Cmd)

$Name = "CANDLE12_V3_OOS2_Runner"
$Store = "D:\cryptoscreener\backend\data\candle12_v3_paper\virtual_paper_mainnet\oos2"

function Procs {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object {
        $_.CommandLine -like "*candle12_v3_oos2_service*supervisor.py*" -or
        $_.CommandLine -like "*app.research.candle12_v3_paper.cli*run*virtual_paper_mainnet/oos2*" }
}

function Show {
    $t = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    $i = if ($t) { Get-ScheduledTaskInfo -TaskName $Name } else { $null }
    "task: " + $(if ($t) { "$($t.State) last_run=$($i.LastRunTime) last_result=$($i.LastTaskResult) next=$($i.NextRunTime)" } else { "not registered" })
    Procs | ForEach-Object { "proc: pid=$($_.ProcessId) parent=$($_.ParentProcessId) $($_.CommandLine)" }
    if (Test-Path "$Store\runner.lock") { "runner.lock: " + (Get-Content "$Store\runner.lock") }
    if (Test-Path "$Store\heartbeats.jsonl") { "last heartbeat: " + (Get-Content "$Store\heartbeats.jsonl" -Tail 1) }
}

function Stop-All {
    Disable-ScheduledTask -TaskName $Name | Out-Null
    Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    Procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

switch ($Cmd) {
    "status" { Show }
    "start" { Enable-ScheduledTask -TaskName $Name | Out-Null; Start-ScheduledTask -TaskName $Name; Start-Sleep 5; Show }
    "stop" { Stop-All; Start-Sleep 2; Show }
    "restart" { Stop-All; Start-Sleep 2; Enable-ScheduledTask -TaskName $Name | Out-Null; Start-ScheduledTask -TaskName $Name; Start-Sleep 5; Show }
}

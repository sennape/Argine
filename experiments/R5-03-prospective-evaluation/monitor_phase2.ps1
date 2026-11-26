# Research 5 Phase 2 -- acquisition monitor (operational progress only: never a timing, a metric or a label).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File experiments\R5-03-prospective-evaluation\monitor_phase2.ps1
#   (optional) -Out <dir> -Python <python.exe> -IntervalSeconds 60
#
# Reads `campaign.py status --json` every IntervalSeconds. A slot that is running or post-processing is shown as
# running, never as failed; an attempt without a live run is shown as interrupted (it counts as a failure at the next
# `run`). Close it with Ctrl+C; closing the monitor does not affect the acquisition.
param(
    [string]$Out = "$env:LOCALAPPDATA\Argine\r5\phase2-evaluation",
    [string]$Python = "$env:USERPROFILE\anaconda3\python.exe",
    [int]$IntervalSeconds = 60
)
$ErrorActionPreference = "Stop"
$Repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Driver = Join-Path $Repo "experiments\R5-03-prospective-evaluation\campaign.py"
$Started = Get-Date
$LastCount = -1
$LastChange = Get-Date
while ($true) {
    try {
        $raw = & $Python $Driver status --out $Out --json 2>&1
        $s = $raw | Out-String | ConvertFrom-Json
    } catch {
        Clear-Host
        Write-Host "R5 Phase 2 monitor: status unavailable ($($_.Exception.Message))" -ForegroundColor Yellow
        Start-Sleep -Seconds $IntervalSeconds
        continue
    }
    if ($s.counted_valid -ne $LastCount) { $LastCount = $s.counted_valid; $LastChange = Get-Date }
    $m = $s.campaigns.'mechanism_1.5GiB'; $t1 = $s.campaigns.'timing_1.5GiB'; $t4 = $s.campaigns.'timing_4GiB'
    Clear-Host
    Write-Host ("R5 Phase 2 acquisition monitor   {0:yyyy-MM-dd HH:mm:ss}   (watching for {1:hh\:mm})" -f (Get-Date), ((Get-Date) - $Started))
    Write-Host ("output: {0}" -f $Out)
    Write-Host ""
    Write-Host ("counted launches valid : {0} / {1}" -f $s.counted_valid, $s.counted_total)
    Write-Host ("  mechanism 1.5 GiB    : {0} / {1}" -f $m.valid, $m.of)
    Write-Host ("  timing    1.5 GiB    : {0} / {1}" -f $t1.valid, $t1.of)
    Write-Host ("  timing    4 GiB      : {0} / {1}" -f $t4.valid, $t4.of)
    Write-Host ("warmups done           : {0}" -f $s.warmups_done)
    $run = if ($s.running) { "{0} ({1})" -f $s.running, $s.running_phase } else { "-" }
    Write-Host ("running                : {0}" -f $run)
    Write-Host ("last progress          : {0:HH:mm:ss} ({1:N0} min ago)" -f $LastChange, ((Get-Date) - $LastChange).TotalMinutes)
    if ($s.pause_requested) { Write-Host "pause requested        : the run stops cleanly after the current launch" -ForegroundColor Cyan }
    if ($s.paused) { Write-Host "PAUSED                 : not a failure; run the acquisition command again to resume" -ForegroundColor Cyan }
    if ($s.interrupted_attempts_counting_as_failures.Count -gt 0) {
        Write-Host ("INTERRUPTED            : {0} (no live run; counts as a failure at the next run)" -f ($s.interrupted_attempts_counting_as_failures -join ", ")) -ForegroundColor Red
    }
    if ($s.failed_slots.Count -gt 0) { Write-Host ("FAILED SLOTS           : {0}" -f ($s.failed_slots -join ", ")) -ForegroundColor Red }
    if ($s.stopped) {
        $kind = if ($s.terminal) { "TERMINAL (see ACQUISITION_STOPPED.json; run analyze)" } else { "STOPPED (blame_rules: rerun only with a listed cause and evidence, or finalize)" }
        Write-Host ("acquisition            : {0}" -f $kind) -ForegroundColor Red
    }
    if ($s.analysis_done) { Write-Host "analysis               : done (PHASE2_RESULT.json written once)" -ForegroundColor Green }
    elseif (-not $s.running -and -not $s.stopped -and -not $s.paused -and $s.counted_valid -lt $s.counted_total -and $s.counted_valid -gt 0) {
        Write-Host "acquisition            : not running" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "No timing, metric or classification is shown before the one-shot analysis."
    Start-Sleep -Seconds $IntervalSeconds
}

# R2-12 host conformance harness (R2-12 Phase F, second rewrite).
#
# This replaces the earlier draft, which was rejected: it waited on
# llama-server.exe as a one-shot batch process (-Wait) and called the
# wrong comparator (a two-trace diff script) with the wrong CLI contract.
# llama-server.exe is a long-running HTTP server: this version starts it
# in the background, polls /health, drives one real /completion request
# per policy (forcing real MoE forward passes through the real retention
# policy), then stops it -- detecting death at every step rather than
# waiting for an exit that, for a long-running server, is never supposed
# to happen on its own.
#
# The real-trace comparator this validation uses is:
#   tools\r2_12_real_trace_conformance.py
# NOT tools\r2_12_retention_conformance_ref.py. It takes exactly one
# trace file and replays it against the frozen Python policy reference,
# printing {"result": "MATCH"|"MISMATCH"|"BLOCKED_INVALID", ...} and
# exiting 0 only on MATCH. It requires every non-blank line to be a full
# decision record (policy_params and all); it does NOT understand this
# project's additive per-event completion lines
# ({"step":...,"complete":1,...}), so this harness filters the trace to
# decision lines (those containing a "policy" key) before handing it to
# the comparator, and checks completion-line evidence itself, separately.
#
# Usage:
#   .\r2-12-host-conformance.ps1 -LlamaServerExe ..\..\Argine2\llama-research-v7\llama-server.exe `
#       -Model <path-to-gguf> -ExpectedSha256 <sha256-of-llama-server.exe>
#
# Exits 0 and prints "R2-12 HOST CONFORMANCE: BOTH MATCH" only when BOTH
# policies (b_selected, predicted_protect) independently: started, served
# /health, produced completion_tokens > 0 from a real /completion request,
# never died unexpectedly, produced a non-empty trace with at least one
# decision row and at least one post-service completion marker, and the
# real-trace comparator returned MATCH with exit code 0 on the filtered
# decision-line trace. Any other outcome exits nonzero with a short,
# specific diagnostic -- this script never prints BOTH MATCH from a
# finally block or after swallowing an exception.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $LlamaServerExe,
    [Parameter(Mandatory = $true)] [string] $Model,
    [string] $ExpectedSha256 = "",
    [string] $Python = "python",
    [string] $WorkDir = ".\r2-12-conformance-run",
    [int]    $Port = 8734,
    [int]    $BudgetMiB = 4096,
    [int]    $NExperts = 128,
    [int]    $ReadyTimeoutSec = 120,
    [int]    $CompletionTimeoutSec = 180
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Comparator = Join-Path $ScriptDir "r2_12_real_trace_conformance.py"

function Fail([string] $Reason) {
    Write-Host "R2-12 HOST CONFORMANCE: FAIL -- $Reason" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $LlamaServerExe)) { Fail "llama-server.exe not found at '$LlamaServerExe'" }
if (-not (Test-Path $Model))          { Fail "model not found at '$Model'" }
if (-not (Test-Path $Comparator))     { Fail "real-trace comparator not found at '$Comparator'" }

if ($ExpectedSha256 -ne "") {
    $actualSha256 = (Get-FileHash -Path $LlamaServerExe -Algorithm SHA256).Hash
    if ($actualSha256.ToLower() -ne $ExpectedSha256.ToLower()) {
        Fail "llama-server.exe SHA-256 mismatch: expected $ExpectedSha256, got $actualSha256 -- refusing to validate the wrong binary"
    }
    Write-Host "runtime SHA-256 verified: $actualSha256"
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$results = @{}

function Test-RunOnePolicy([string] $PolicyName, [hashtable] $PolicyEnv) {
    $tracePath    = Join-Path $WorkDir "$PolicyName.trace.jsonl"
    $decisionPath = Join-Path $WorkDir "$PolicyName.decisions.jsonl"
    $stdoutPath   = Join-Path $WorkDir "$PolicyName.stdout.log"
    $stderrPath   = Join-Path $WorkDir "$PolicyName.stderr.log"
    foreach ($p in @($tracePath, $decisionPath, $stdoutPath, $stderrPath)) {
        if (Test-Path $p) { Remove-Item $p -Force }
    }

    # Mandatory for this validation: never let a leftover ARGINE_PREFETCH
    # confound the run (R2-12 Phase F found and fixed a real hazard behind
    # it in v6/v7; this validation is about the retention-policy fix, not
    # that one, so it stays explicitly off here regardless of what the
    # calling shell has set).
    $env:ARGINE_PREFETCH = $null
    Remove-Item Env:\ARGINE_PREFETCH -ErrorAction SilentlyContinue

    $env:ARGINE_EXPERT_READ        = "blocco"
    $env:ARGINE_EXPERT_SHARDS      = "1"
    $env:ARGINE_EXPERT_BUDGET      = [string] ($BudgetMiB * 1MB)
    $env:ARGINE_RETENTION_TRACE    = $tracePath
    $env:ARGINE_RETENTION_POLICY   = $PolicyName
    foreach ($k in $PolicyEnv.Keys) { Set-Item -Path "env:$k" -Value $PolicyEnv[$k] }

    $proc = $null
    try {
        $proc = Start-Process -FilePath $LlamaServerExe `
            -ArgumentList @("-m", $Model, "--n-gpu-layers", "0", "--port", $Port, "--host", "127.0.0.1") `
            -NoNewWindow -PassThru `
            -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

        # --- require runtime ready: poll /health, detect death meanwhile ---
        $ready = $false
        $deadline = (Get-Date).AddSeconds($ReadyTimeoutSec)
        while ((Get-Date) -lt $deadline) {
            if ($proc.HasExited) {
                return @{ Success = $false; Reason = "$PolicyName: llama-server.exe exited during startup, before becoming ready (exit code $($proc.ExitCode))" }
            }
            try {
                $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3 -ErrorAction Stop
                if ($health.status -eq "ok") { $ready = $true; break }
            } catch {
                Start-Sleep -Milliseconds 500
            }
        }
        if (-not $ready) {
            return @{ Success = $false; Reason = "$PolicyName: did not become ready within ${ReadyTimeoutSec}s (no successful /health response)" }
        }

        # --- require completion_tokens > 0: one real /completion request ---
        $completionBody = @{
            prompt    = "The quick brown fox jumps over the lazy dog, and then"
            n_predict = 64
        } | ConvertTo-Json
        $completionTokens = 0
        try {
            $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/completion" -Method Post `
                -Body $completionBody -ContentType "application/json" -TimeoutSec $CompletionTimeoutSec -ErrorAction Stop
            if ($resp.PSObject.Properties.Name -contains "tokens_predicted") {
                $completionTokens = [int] $resp.tokens_predicted
            } elseif ($resp.PSObject.Properties.Name -contains "completion_tokens") {
                $completionTokens = [int] $resp.completion_tokens
            }
        } catch {
            if ($proc.HasExited) {
                return @{ Success = $false; Reason = "$PolicyName: llama-server.exe died during the /completion request (exit code $($proc.ExitCode)) -- $($_.Exception.Message)" }
            }
            return @{ Success = $false; Reason = "$PolicyName: /completion request failed without the process dying -- $($_.Exception.Message)" }
        }
        if ($completionTokens -le 0) {
            return @{ Success = $false; Reason = "$PolicyName: /completion responded but completion_tokens/tokens_predicted was $completionTokens, not > 0" }
        }

        if ($proc.HasExited) {
            return @{ Success = $false; Reason = "$PolicyName: llama-server.exe died immediately after a successful /completion response (exit code $($proc.ExitCode)) -- this is exactly the silent post-trace death this validation exists to catch" }
        }
    } finally {
        if ($proc -and -not $proc.HasExited) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }

    # --- require non-empty JSONL + post-service completion evidence ------
    if (-not (Test-Path $tracePath)) {
        return @{ Success = $false; Reason = "$PolicyName: no trace file was written at all ($tracePath)" }
    }
    $lines = Get-Content $tracePath -ErrorAction Stop
    if ($lines.Count -eq 0) {
        return @{ Success = $false; Reason = "$PolicyName: trace file exists but has zero rows" }
    }
    $decisionLines   = $lines | Where-Object { $_ -match '"policy"\s*:' }
    $completionLines = $lines | Where-Object { $_ -match '"complete"\s*:\s*1' }
    if ($decisionLines.Count -eq 0) {
        return @{ Success = $false; Reason = "$PolicyName: $($lines.Count) trace row(s) but zero decision rows (no '\"policy\":' field anywhere)" }
    }
    if ($completionLines.Count -eq 0) {
        return @{ Success = $false; Reason = "$PolicyName: $($decisionLines.Count) decision row(s) but zero post-service completion markers -- every event's decision was traced but Fase 1 never finished for any of them" }
    }
    Set-Content -Path $decisionPath -Value $decisionLines

    # --- real-trace comparator: exit code is load-bearing, not just text --
    $comparatorOut = & $Python $Comparator $decisionPath 2>&1
    $comparatorExit = $LASTEXITCODE
    $parsed = $null
    try { $parsed = $comparatorOut | ConvertFrom-Json } catch { }

    if ($comparatorExit -ne 0) {
        $reason = if ($parsed -and $parsed.PSObject.Properties.Name -contains "reason") { $parsed.reason } else { "$comparatorOut" }
        return @{ Success = $false; Reason = "$PolicyName: comparator exited $comparatorExit ($($parsed.result)): $reason" }
    }
    if (-not $parsed -or $parsed.result -ne "MATCH") {
        return @{ Success = $false; Reason = "$PolicyName: comparator exit code was 0 but result was not an unambiguous MATCH: $comparatorOut" }
    }

    return @{
        Success          = $true
        CompletionTokens = $completionTokens
        RowCount         = $lines.Count
        DecisionCount    = $decisionLines.Count
        CompletionCount  = $completionLines.Count
        ComparatorResult = $parsed.result
        EventsCompared   = $parsed.events_compared
    }
}

$results["b_selected"] = Test-RunOnePolicy -PolicyName "b_selected" -PolicyEnv @{
    ARGINE_RETENTION_DECAY    = "0.99"
    ARGINE_RETENTION_INTERVAL = "64"
}
$results["predicted_protect"] = Test-RunOnePolicy -PolicyName "predicted_protect" -PolicyEnv @{
    ARGINE_RETENTION_DECAY          = "0.99"
    ARGINE_RETENTION_INTERVAL       = "64"
    ARGINE_RETENTION_PREDICTOR_DECAY = "0.99"
    ARGINE_RETENTION_TOPK           = "8"
    ARGINE_RETENTION_NEXPERTS       = [string] $NExperts
}

$bSelectedOk        = [bool] $results["b_selected"].Success
$predictedProtectOk = [bool] $results["predicted_protect"].Success

foreach ($name in @("b_selected", "predicted_protect")) {
    $r = $results[$name]
    if ($r.Success) {
        Write-Host "$name : OK (tokens=$($r.CompletionTokens), righe=$($r.RowCount), decisioni=$($r.DecisionCount), completamenti=$($r.CompletionCount), comparator=$($r.ComparatorResult), eventi_confrontati=$($r.EventsCompared))"
    } else {
        Write-Host "$name : FAIL -- $($r.Reason)" -ForegroundColor Red
    }
}

# Explicit, independent booleans; the gate is their logical AND, computed
# here and only here -- never inferred from "no exception was thrown".
if (-not ($bSelectedOk -and $predictedProtectOk)) {
    Fail "at least one policy did not pass: b_selected=$bSelectedOk predicted_protect=$predictedProtectOk"
}

Write-Host ""
Write-Host "runtime: $LlamaServerExe"
Write-Host "b_selected:        tokens=$($results['b_selected'].CompletionTokens) decisioni=$($results['b_selected'].DecisionCount) completamenti=$($results['b_selected'].CompletionCount) comparator=$($results['b_selected'].ComparatorResult)"
Write-Host "predicted_protect: tokens=$($results['predicted_protect'].CompletionTokens) decisioni=$($results['predicted_protect'].DecisionCount) completamenti=$($results['predicted_protect'].CompletionCount) comparator=$($results['predicted_protect'].ComparatorResult)"
Write-Host "R2-12 HOST CONFORMANCE: BOTH MATCH" -ForegroundColor Green
exit 0

# R2-12 host conformance harness (R2-12 Phase F, third rewrite).
#
# ROOT CAUSE of the previous run's zero traces, demonstrated by reading
# argine-experts.cpp (patches/argine-experts.cpp, Contesto::inizializza):
#
#   const char * path = env_due_nomi("ARGINE_EXPERT_STORE");
#   if (!path || !*path) {
#       return;  // non configurato: il buffer type non si registra
#   }
#
# The second harness draft never set ARGINE_EXPERT_STORE at all. Without
# it, Contesto::inizializza() returns immediately: the custom buffer
# type never registers, c.attivo is never set, the .psxs store is never
# opened, the retention policy is never attached, and
# ARGINE_RETENTION_TRACE is never even looked at (its own getenv call
# site is unreachable). llama-server.exe then runs as plain, unmodified
# llama.cpp: it loads the GGUF normally, can still answer /health and
# /completion, and produces zero trace output -- exactly, identically,
# for both policies, with no other symptom. This is a harness
# environment-construction defect, not a v7 runtime or policy defect.
#
# Second, related defect: trace/store paths were relative
# (".\r2-12-conformance-run\..."), resolved against whatever directory
# happened to be current when the script ran -- not guaranteed to match
# the child process's own working directory. Every artifact path below
# is now resolved to an absolute path with [System.IO.Path]::GetFullPath
# before the child ever starts.
#
# Third: the child process was started with Start-Process relying on
# ambient $env:* mutation for environment inheritance. This version
# builds the child's environment explicitly on a
# System.Diagnostics.ProcessStartInfo instance (whose
# EnvironmentVariables collection starts as a copy of the current
# process's own environment, then has every required key added/
# overridden by name, and ARGINE_PREFETCH removed outright) so the
# exact child environment is inspectable and testable without ever
# launching llama-server.exe -- see Test-ArgineHarnessSelfTest below and
# the -SelfTest switch.
#
# Usage (real host run):
#   .\r2-12-host-conformance.ps1 -LlamaServerExe C:\Users\alpel\Desktop\Argine2\llama-research-v7\llama-server.exe `
#       -Model C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf `
#       -ExpertStore C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.psxs `
#       -ExpectedSha256 cec063535698e394139c7cc93b8f2aa63200e45f17894fb439b21672e6a0183a
#
# Self-test only (no model, no llama-server.exe launch):
#   .\r2-12-host-conformance.ps1 -SelfTest

[CmdletBinding()]
param(
    [string] $LlamaServerExe,
    [string] $Model,
    [string] $ExpertStore = "",
    [string] $ExpectedSha256 = "",
    [string] $Python = "python",
    [string] $WorkDir = "",
    [int]    $Port = 8734,
    [int]    $BudgetMiB = 4096,
    [int]    $NExperts = 128,
    [int]    $ReadyTimeoutSec = 120,
    [int]    $MarkerTimeoutSec = 180,
    [int]    $CompletionTimeoutSec = 180,
    [switch] $SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Comparator = Join-Path $ScriptDir "r2_12_real_trace_conformance.py"

function Resolve-AbsolutePath {
    param([Parameter(Mandatory = $true)][string] $Path)
    # [System.IO.Path]::GetFullPath resolves against the CURRENT process's
    # working directory, not any assumed script/model directory -- exactly
    # why every artifact path in this harness is run through this function
    # once, up front, rather than built as a relative Join-Path later.
    return [System.IO.Path]::GetFullPath($Path)
}

# ---------------------------------------------------------------------------
# Pure construction functions: no process launch, no file I/O beyond the
# existence checks the caller explicitly asks for. These are exactly what
# Test-ArgineHarnessSelfTest exercises without ever touching llama-server.exe.
# ---------------------------------------------------------------------------

function New-ArgineChildEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]   $PolicyName,
        [Parameter(Mandatory = $true)][string]   $AbsExpertStore,
        [Parameter(Mandatory = $true)][string]   $AbsTracePath,
        [Parameter(Mandatory = $true)][uint64]   $BudgetBytes,
        [Parameter(Mandatory = $true)][int]      $NExperts,
        [Parameter(Mandatory = $true)][hashtable] $PolicySpecific
    )
    # Every variable argine-experts.cpp's Contesto::inizializza() and
    # argine-store.cpp's parse_retention_config_from_env() actually read,
    # set explicitly and unconditionally -- no reliance on anything the
    # calling shell happened to have set, and ARGINE_PREFETCH forced off
    # for this validation regardless of ambient state (see 0005: a real,
    # separately-fixed hazard, irrelevant to and not re-tested by this
    # retention-policy validation).
    $envMap = [ordered]@{
        ARGINE_EXPERT_STORE   = $AbsExpertStore
        ARGINE_EXPERT_BUDGET  = [string] $BudgetBytes
        ARGINE_EXPERT_SHARDS  = "1"
        ARGINE_EXPERT_READ    = "blocco"
        ARGINE_RETENTION_POLICY = $PolicyName
        ARGINE_RETENTION_TRACE  = $AbsTracePath
        ARGINE_PREFETCH         = $null   # explicitly absent, not just empty
    }
    foreach ($k in $PolicySpecific.Keys) { $envMap[$k] = $PolicySpecific[$k] }
    return $envMap
}

function Get-ArginePolicySpecificEnv {
    param([Parameter(Mandatory = $true)][string] $PolicyName, [Parameter(Mandatory = $true)][int] $NExperts)
    # The three frozen (decay, interval) B_selected(M) pairs are
    # (0.999,1) / (0.99,64) / (0.999,16) -- argine-retention-policy.cpp
    # refuses anything else outright. (0.99, 64) is used here for both
    # policies, matching the pairing already exercised by
    # patches/argine-retention-integration-test.cpp sections 11-12 and
    # patches/argine-partial-kind-admission-test.cpp.
    if ($PolicyName -eq "b_selected") {
        return @{
            ARGINE_RETENTION_DECAY    = "0.99"
            ARGINE_RETENTION_INTERVAL = "64"
        }
    }
    if ($PolicyName -eq "predicted_protect") {
        return @{
            ARGINE_RETENTION_DECAY           = "0.99"
            ARGINE_RETENTION_INTERVAL        = "64"
            ARGINE_RETENTION_PREDICTOR_DECAY = "0.99"
            ARGINE_RETENTION_TOPK            = "8"
            ARGINE_RETENTION_NEXPERTS        = [string] $NExperts
        }
    }
    throw "Get-ArginePolicySpecificEnv: unknown policy '$PolicyName'"
}

function New-ArgineChildArguments {
    param(
        [Parameter(Mandatory = $true)][string] $AbsModel,
        [Parameter(Mandatory = $true)][int]    $Port
    )
    return @("-m", $AbsModel, "--n-gpu-layers", "0", "--port", [string] $Port, "--host", "127.0.0.1")
}

function New-ArgineProcessStartInfo {
    param(
        [Parameter(Mandatory = $true)][string]   $AbsExe,
        [Parameter(Mandatory = $true)][string[]] $Arguments,
        [Parameter(Mandatory = $true)][System.Collections.Specialized.OrderedDictionary] $EnvMap,
        [Parameter(Mandatory = $true)][string]   $AbsWorkDir,
        [Parameter(Mandatory = $true)][string]   $AbsStdoutPath,
        [Parameter(Mandatory = $true)][string]   $AbsStderrPath
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName  = $AbsExe
    # Built as a single quoted .Arguments string, not .ArgumentList --
    # ArgumentList is absent on some older .NET Framework builds that
    # Windows PowerShell 5.1 may be hosted on; a manually quoted string
    # works on every version and is still a pure, testable function.
    $psi.Arguments = ($Arguments | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }) -join ' '
    $psi.WorkingDirectory       = $AbsWorkDir
    $psi.UseShellExecute        = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    # .EnvironmentVariables starts as a live copy of THIS process's own
    # environment (ProcessStartInfo's documented default): every key in
    # EnvMap is then added or overridden by name on top of that, and a
    # $null value removes the key outright -- the child never sees a
    # stale value for a key this harness cares about, and still inherits
    # PATH and everything else unrelated.
    foreach ($k in $EnvMap.Keys) {
        if ($null -eq $EnvMap[$k]) {
            if ($psi.EnvironmentVariables.ContainsKey($k)) { $psi.EnvironmentVariables.Remove($k) }
        } else {
            $psi.EnvironmentVariables[$k] = $EnvMap[$k]
        }
    }
    return @{ StartInfo = $psi; StdoutPath = $AbsStdoutPath; StderrPath = $AbsStderrPath }
}

# ---------------------------------------------------------------------------
# Self-test: proves the pure functions above without launching anything.
# ---------------------------------------------------------------------------

function Test-ArgineHarnessSelfTest {
    # $script:ArgineSelfTestFailures, not a function-local $failures: a
    # nested function assigning "$script:failures++" would target the
    # real top-level script scope while this function's own local
    # $failures sits in a different (function) scope -- two different
    # variables with the same short name. Under Set-StrictMode -Version
    # Latest that mismatch throws on first use rather than just
    # miscounting, so both the declaration and every increment below use
    # the identical, explicitly-scoped name.
    $script:ArgineSelfTestFailures = 0
    function Assert([bool] $cond, [string] $what) {
        if ($cond) { Write-Host "  [ok] $what" }
        else { Write-Host "  [NO] $what" -ForegroundColor Red; $script:ArgineSelfTestFailures++ }
    }

    Write-Host "1. absolute paths"
    $relTrace = Resolve-AbsolutePath ".\r2-12-conformance-run\b_selected.trace.jsonl"
    Assert ([System.IO.Path]::IsPathRooted($relTrace)) "a relative trace path resolves to a rooted absolute path"
    $relStore = Resolve-AbsolutePath "..\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.psxs"
    Assert ([System.IO.Path]::IsPathRooted($relStore)) "a relative expert-store path resolves to a rooted absolute path"

    Write-Host "2. child environment contains every required ARGINE_* key"
    $bEnv = Get-ArginePolicySpecificEnv -PolicyName "b_selected" -NExperts 128
    $env1 = New-ArgineChildEnvironment -PolicyName "b_selected" -AbsExpertStore "C:\modelli\m.psxs" `
        -AbsTracePath "C:\run\b_selected.trace.jsonl" -BudgetBytes 4294967296 -NExperts 128 -PolicySpecific $bEnv
    foreach ($req in @("ARGINE_EXPERT_STORE","ARGINE_EXPERT_BUDGET","ARGINE_EXPERT_SHARDS","ARGINE_EXPERT_READ",
                        "ARGINE_RETENTION_POLICY","ARGINE_RETENTION_TRACE","ARGINE_RETENTION_DECAY","ARGINE_RETENTION_INTERVAL")) {
        Assert ($env1.Contains($req)) "b_selected child env has key $req"
    }
    Assert ($env1["ARGINE_EXPERT_STORE"] -eq "C:\modelli\m.psxs") "ARGINE_EXPERT_STORE carries the exact absolute store path given"
    Assert ($env1["ARGINE_EXPERT_SHARDS"] -eq "1") "ARGINE_EXPERT_SHARDS is forced to 1 (required for any retention policy)"
    Assert ($env1["ARGINE_EXPERT_READ"] -eq "blocco") "ARGINE_EXPERT_READ is forced to blocco (the only mode that consults the policy)"
    Assert ($env1["ARGINE_RETENTION_DECAY"] -eq "0.99" -and $env1["ARGINE_RETENTION_INTERVAL"] -eq "64") "b_selected gets a frozen (decay,interval) pair: (0.99,64)"
    Assert ($env1.Contains("ARGINE_PREFETCH") -and $null -eq $env1["ARGINE_PREFETCH"]) "ARGINE_PREFETCH is explicitly marked for removal, not left ambient"

    Write-Host "3. predicted_protect gets its extra frozen parameters"
    $ppEnv = Get-ArginePolicySpecificEnv -PolicyName "predicted_protect" -NExperts 128
    $env2 = New-ArgineChildEnvironment -PolicyName "predicted_protect" -AbsExpertStore "C:\modelli\m.psxs" `
        -AbsTracePath "C:\run\predicted_protect.trace.jsonl" -BudgetBytes 4294967296 -NExperts 128 -PolicySpecific $ppEnv
    foreach ($req in @("ARGINE_RETENTION_PREDICTOR_DECAY","ARGINE_RETENTION_TOPK","ARGINE_RETENTION_NEXPERTS")) {
        Assert ($env2.Contains($req)) "predicted_protect child env has key $req"
    }
    Assert ($env2["ARGINE_RETENTION_PREDICTOR_DECAY"] -eq "0.99") "predictor decay is the frozen 0.99"
    Assert ($env2["ARGINE_RETENTION_NEXPERTS"] -eq "128") "NExperts flows through to ARGINE_RETENTION_NEXPERTS"

    Write-Host "4. unknown policy name is rejected, not silently ignored"
    $threw = $false
    try { Get-ArginePolicySpecificEnv -PolicyName "nope" -NExperts 128 | Out-Null } catch { $threw = $true }
    Assert $threw "Get-ArginePolicySpecificEnv throws on an unrecognized policy name"

    Write-Host "5. ProcessStartInfo actually carries every env key (not just a hashtable we never attach)"
    $built = New-ArgineProcessStartInfo -AbsExe "C:\bin\llama-server.exe" -Arguments @("-m","C:\m.gguf") `
        -EnvMap $env1 -AbsWorkDir "C:\run" -AbsStdoutPath "C:\run\out.log" -AbsStderrPath "C:\run\err.log"
    $psi = $built.StartInfo
    Assert ($psi.FileName -eq "C:\bin\llama-server.exe") "ProcessStartInfo.FileName is the exact exe path given"
    Assert ($psi.WorkingDirectory -eq "C:\run") "ProcessStartInfo.WorkingDirectory is set explicitly, not left to the caller's cwd"
    foreach ($req in @("ARGINE_EXPERT_STORE","ARGINE_EXPERT_BUDGET","ARGINE_EXPERT_SHARDS","ARGINE_EXPERT_READ",
                        "ARGINE_RETENTION_POLICY","ARGINE_RETENTION_TRACE")) {
        Assert ($psi.EnvironmentVariables.ContainsKey($req)) "ProcessStartInfo.EnvironmentVariables actually contains $req"
    }
    Assert (-not $psi.EnvironmentVariables.ContainsKey("ARGINE_PREFETCH")) "ProcessStartInfo.EnvironmentVariables does NOT contain ARGINE_PREFETCH"
    Assert ($psi.EnvironmentVariables.ContainsKey("PATH") -or $psi.EnvironmentVariables.ContainsKey("Path")) "unrelated ambient variables (PATH) are still inherited, not wiped"

    Write-Host "6. comparator path and child argument construction"
    Assert ((Split-Path -Leaf $Comparator) -eq "r2_12_real_trace_conformance.py") "the comparator path points at r2_12_real_trace_conformance.py, not r2_12_retention_conformance_ref.py"
    $args = New-ArgineChildArguments -AbsModel "C:\m.gguf" -Port 8734
    Assert ($args -contains "-m" -and $args -contains "C:\m.gguf") "child arguments include the model path"
    Assert ($args -contains "--port" -and $args -contains "8734") "child arguments include the requested port"

    Write-Host "7. BOTH MATCH is structurally impossible unless both booleans are true"
    $gateSrc = Get-Content -Raw $PSCommandPath
    Assert ($gateSrc -match '\(\s*\$bSelectedOk\s+-and\s+\$predictedProtectOk\s*\)') "the final gate is an explicit boolean AND of two named success flags, found in this script's own source"
    Assert (-not ($gateSrc -match 'finally\s*\{[^}]*BOTH MATCH')) "'BOTH MATCH' text does not appear inside any finally block in this script"

    Write-Host "8. PowerShell parser hazards from the previous draft are absent"
    Assert (-not ($gateSrc -match '\$\w+:[^\s"]')) 'no bare "$Variable:" interpolation (must be "${Variable}:") appears anywhere in this script'
    Assert (-not ($gateSrc -match '\\"')) 'no C-style \" escape sequence appears anywhere in this script'

    Write-Host ""
    if ($script:ArgineSelfTestFailures -eq 0) {
        Write-Host "SELF-TEST OK: 0 failures" -ForegroundColor Green
        return $true
    } else {
        Write-Host "SELF-TEST FAILED: $($script:ArgineSelfTestFailures) failure(s)" -ForegroundColor Red
        return $false
    }
}

if ($SelfTest) {
    $ok = Test-ArgineHarnessSelfTest
    # Deliberately not the PS7+-only "? :" ternary -- this harness must
    # parse and run under Windows PowerShell 5.1 as well as pwsh 7+.
    if ($ok) { exit 0 } else { exit 1 }
}

# ---------------------------------------------------------------------------
# Real host run from here on.
# ---------------------------------------------------------------------------

function Fail([string] $Reason) {
    Write-Host "R2-12 HOST CONFORMANCE: FAIL -- $Reason" -ForegroundColor Red
    exit 1
}

if (-not $LlamaServerExe -or -not $Model) { Fail "both -LlamaServerExe and -Model are required for a real run (use -SelfTest for the no-model check)" }

$AbsExe   = Resolve-AbsolutePath $LlamaServerExe
$AbsModel = Resolve-AbsolutePath $Model
if (-not (Test-Path $AbsExe))   { Fail "llama-server.exe not found at '$AbsExe'" }
if (-not (Test-Path $AbsModel)) { Fail "model not found at '$AbsModel'" }

# -ExpertStore is mandatory, with a derived default ONLY if that derived
# file actually exists -- never an implicit, unverified .gguf -> .psxs
# guess (point 6 of the audit).
if ($ExpertStore -eq "") {
    $derived = [System.IO.Path]::ChangeExtension($AbsModel, ".psxs")
    if (Test-Path $derived) {
        $ExpertStore = $derived
        Write-Host "note: -ExpertStore not given, derived and verified to exist: $derived"
    } else {
        Fail "no -ExpertStore given, and the derived path '$derived' does not exist. Pass -ExpertStore explicitly."
    }
}
$AbsStore = Resolve-AbsolutePath $ExpertStore
if (-not (Test-Path $AbsStore)) { Fail "expert store (.psxs) not found at '$AbsStore'" }

if (-not (Test-Path $Comparator)) { Fail "real-trace comparator not found at '$Comparator'" }

if ($ExpectedSha256 -ne "") {
    $actualSha256 = (Get-FileHash -Path $AbsExe -Algorithm SHA256).Hash
    if ($actualSha256.ToLower() -ne $ExpectedSha256.ToLower()) {
        Fail "llama-server.exe SHA-256 mismatch: expected $ExpectedSha256, got $actualSha256"
    }
    Write-Host "IDENTITY: runtime SHA-256 verified ($actualSha256)"
} else {
    Write-Host "IDENTITY: no -ExpectedSha256 given, skipping verification (not recommended)"
}

if ($WorkDir -eq "") { $WorkDir = Join-Path $ScriptDir "r2-12-conformance-run" }
$AbsWorkDir = Resolve-AbsolutePath $WorkDir
New-Item -ItemType Directory -Force -Path $AbsWorkDir | Out-Null

function Wait-ForLogMarker {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process] $Proc,
        [Parameter(Mandatory = $true)][string] $LogPath,
        [Parameter(Mandatory = $true)][string] $Pattern,
        [Parameter(Mandatory = $true)][int]    $TimeoutSec
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if ($Proc.HasExited) { return @{ Found = $false; Died = $true } }
        if (Test-Path $LogPath) {
            $text = Get-Content -Raw -ErrorAction SilentlyContinue $LogPath
            if ($text -and ($text -match $Pattern)) { return @{ Found = $true; Died = $false } }
        }
        Start-Sleep -Milliseconds 300
    }
    return @{ Found = $false; Died = $Proc.HasExited }
}

function Test-RunOnePolicy {
    param([Parameter(Mandatory = $true)][string] $PolicyName)

    $tracePath  = Resolve-AbsolutePath (Join-Path $AbsWorkDir "$PolicyName.trace.jsonl")
    $decisionPath = Resolve-AbsolutePath (Join-Path $AbsWorkDir "$PolicyName.decisions.jsonl")
    $stdoutPath = Resolve-AbsolutePath (Join-Path $AbsWorkDir "$PolicyName.stdout.log")
    $stderrPath = Resolve-AbsolutePath (Join-Path $AbsWorkDir "$PolicyName.stderr.log")
    foreach ($p in @($tracePath, $decisionPath, $stdoutPath, $stderrPath)) {
        if (Test-Path $p) { Remove-Item $p -Force }
    }

    $policySpecific = Get-ArginePolicySpecificEnv -PolicyName $PolicyName -NExperts $NExperts
    $envMap = New-ArgineChildEnvironment -PolicyName $PolicyName -AbsExpertStore $AbsStore `
        -AbsTracePath $tracePath -BudgetBytes ([uint64] $BudgetMiB * 1048576) -NExperts $NExperts `
        -PolicySpecific $policySpecific

    $built = New-ArgineProcessStartInfo -AbsExe $AbsExe -Arguments (New-ArgineChildArguments -AbsModel $AbsModel -Port $Port) `
        -EnvMap $envMap -AbsWorkDir $AbsWorkDir -AbsStdoutPath $stdoutPath -AbsStderrPath $stderrPath
    $psi = $built.StartInfo

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $stdoutWriter = [System.IO.StreamWriter]::new($stdoutPath, $false)
    $stderrWriter = [System.IO.StreamWriter]::new($stderrPath, $false)
    $stdoutWriter.AutoFlush = $true
    $stderrWriter.AutoFlush = $true
    Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived -Action {
        if ($null -ne $Event.SourceEventArgs.Data) { $Event.MessageData.WriteLine($Event.SourceEventArgs.Data) }
    } -MessageData $stdoutWriter | Out-Null
    Register-ObjectEvent -InputObject $proc -EventName ErrorDataReceived -Action {
        if ($null -ne $Event.SourceEventArgs.Data) { $Event.MessageData.WriteLine($Event.SourceEventArgs.Data) }
    } -MessageData $stderrWriter | Out-Null

    try {
        [void] $proc.Start()
        $proc.BeginOutputReadLine()
        $proc.BeginErrorReadLine()

        # --- STORE READY + POLICY READY: "argine: retention policy attiva"
        # is printed by Contesto::inizializza() ONLY after psx_open() and
        # psx_cache_attach_retention_policy() both succeeded -- it is
        # unreachable if ARGINE_EXPERT_STORE was missing or invalid, or if
        # the policy failed to attach. ---
        $policyMarker = Wait-ForLogMarker -Proc $proc -LogPath $stderrPath -Pattern "argine: retention policy attiva" -TimeoutSec $MarkerTimeoutSec
        if (-not $policyMarker.Found) {
            $reason = if ($policyMarker.Died) { "llama-server.exe exited (code $($proc.ExitCode)) before the 'retention policy attiva' marker appeared" }
                      else { "'argine: retention policy attiva' never appeared in stderr within ${MarkerTimeoutSec}s -- the expert store or the policy never activated" }
            return @{ Success = $false; Reason = "${PolicyName}: STORE/POLICY READY failed -- $reason" }
        }
        Write-Host "${PolicyName}: STORE READY / POLICY READY ok"

        # --- TRACE OPEN: "ARGINE_RETENTION_TRACE: aperta con successo" ---
        $traceMarker = Wait-ForLogMarker -Proc $proc -LogPath $stderrPath -Pattern "ARGINE_RETENTION_TRACE: aperta con successo" -TimeoutSec $MarkerTimeoutSec
        if (-not $traceMarker.Found) {
            $reason = if ($traceMarker.Died) { "llama-server.exe exited (code $($proc.ExitCode)) before the trace-open marker appeared" }
                      else { "trace-open marker never appeared within ${MarkerTimeoutSec}s" }
            return @{ Success = $false; Reason = "${PolicyName}: TRACE OPEN failed -- $reason" }
        }
        Write-Host "${PolicyName}: TRACE OPEN ok ($tracePath)"

        # --- runtime ready: /health, with death detection in the loop ---
        $ready = $false
        $deadline = (Get-Date).AddSeconds($ReadyTimeoutSec)
        while ((Get-Date) -lt $deadline) {
            if ($proc.HasExited) { return @{ Success = $false; Reason = "${PolicyName}: exited during startup (code $($proc.ExitCode))" } }
            try {
                $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3 -ErrorAction Stop
                if ($health.status -eq "ok") { $ready = $true; break }
            } catch { Start-Sleep -Milliseconds 500 }
        }
        if (-not $ready) { return @{ Success = $false; Reason = "${PolicyName}: /health never returned ok within ${ReadyTimeoutSec}s" } }
        Write-Host "${PolicyName}: runtime ready (/health ok)"

        # --- REQUEST: one real /completion ---
        $completionBody = @{ prompt = "The quick brown fox jumps over the lazy dog, and then"; n_predict = 64 } | ConvertTo-Json
        $completionTokens = 0
        try {
            $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/completion" -Method Post `
                -Body $completionBody -ContentType "application/json" -TimeoutSec $CompletionTimeoutSec -ErrorAction Stop
            if ($resp.PSObject.Properties.Name -contains "tokens_predicted") { $completionTokens = [int] $resp.tokens_predicted }
            elseif ($resp.PSObject.Properties.Name -contains "completion_tokens") { $completionTokens = [int] $resp.completion_tokens }
        } catch {
            if ($proc.HasExited) { return @{ Success = $false; Reason = "${PolicyName}: died during /completion (code $($proc.ExitCode)) -- $($_.Exception.Message)" } }
            return @{ Success = $false; Reason = "${PolicyName}: /completion failed without the process dying -- $($_.Exception.Message)" }
        }
        if ($completionTokens -le 0) { return @{ Success = $false; Reason = "${PolicyName}: completion_tokens/tokens_predicted was $completionTokens, not > 0" } }
        Write-Host "${PolicyName}: REQUEST ok (tokens=$completionTokens)"

        # --- POST-SERVICE: the server must still be alive right after ---
        if ($proc.HasExited) { return @{ Success = $false; Reason = "${PolicyName}: died immediately after a successful /completion response (code $($proc.ExitCode))" } }

        # --- first real MoE node served ---
        $nodeMarker = Wait-ForLogMarker -Proc $proc -LogPath $stderrPath -Pattern "argine: expert store IN USO" -TimeoutSec 5
        if (-not $nodeMarker.Found -and -not $nodeMarker.Died) {
            Write-Host "${PolicyName}: note: 'expert store IN USO' marker not seen yet (non-fatal; trace evidence below is authoritative)"
        }
    } finally {
        $stdoutWriter.Flush(); $stderrWriter.Flush()
        if (-not $proc.HasExited) { $proc.Kill($true) }
        Start-Sleep -Milliseconds 200
        $stdoutWriter.Close(); $stderrWriter.Close()
    }

    if (-not (Test-Path $tracePath)) { return @{ Success = $false; Reason = "${PolicyName}: no trace file at '$tracePath' despite a TRACE OPEN marker -- investigate disk/permissions" } }
    $lines = Get-Content $tracePath -ErrorAction Stop
    if ($lines.Count -eq 0) { return @{ Success = $false; Reason = "${PolicyName}: trace file exists but has zero rows" } }
    $decisionLines   = $lines | Where-Object { $_ -match '"policy"\s*:' }
    $completionLines = $lines | Where-Object { $_ -match '"complete"\s*:\s*1' }
    if ($decisionLines.Count -eq 0) { return @{ Success = $false; Reason = "${PolicyName}: $($lines.Count) row(s), zero decision rows" } }
    if ($completionLines.Count -eq 0) { return @{ Success = $false; Reason = "${PolicyName}: $($decisionLines.Count) decision row(s), zero post-service completion markers" } }
    Set-Content -Path $decisionPath -Value $decisionLines
    Write-Host "${PolicyName}: POST-SERVICE ok (righe=$($lines.Count) decisioni=$($decisionLines.Count) completamenti=$($completionLines.Count))"

    $comparatorOut = & $Python $Comparator $decisionPath 2>&1
    $comparatorExit = $LASTEXITCODE
    $parsed = $null
    try { $parsed = $comparatorOut | ConvertFrom-Json } catch { }
    if ($comparatorExit -ne 0) {
        $reason = if ($parsed -and $parsed.PSObject.Properties.Name -contains "reason") { $parsed.reason } else { "$comparatorOut" }
        return @{ Success = $false; Reason = "${PolicyName}: COMPARATOR exited $comparatorExit ($($parsed.result)): $reason" }
    }
    if (-not $parsed -or $parsed.result -ne "MATCH") {
        return @{ Success = $false; Reason = "${PolicyName}: COMPARATOR exit 0 but no unambiguous MATCH: $comparatorOut" }
    }
    Write-Host "${PolicyName}: COMPARATOR ok (MATCH, eventi=$($parsed.events_compared))"

    return @{
        Success = $true; CompletionTokens = $completionTokens; RowCount = $lines.Count
        DecisionCount = $decisionLines.Count; CompletionCount = $completionLines.Count
        ComparatorResult = $parsed.result; EventsCompared = $parsed.events_compared
    }
}

$results = @{}
$results["b_selected"]        = Test-RunOnePolicy -PolicyName "b_selected"
$results["predicted_protect"] = Test-RunOnePolicy -PolicyName "predicted_protect"

$bSelectedOk        = [bool] $results["b_selected"].Success
$predictedProtectOk = [bool] $results["predicted_protect"].Success

foreach ($name in @("b_selected", "predicted_protect")) {
    $r = $results[$name]
    if ($r.Success) {
        Write-Host "${name}: OK (tokens=$($r.CompletionTokens) righe=$($r.RowCount) decisioni=$($r.DecisionCount) completamenti=$($r.CompletionCount) comparator=$($r.ComparatorResult))"
    } else {
        Write-Host "${name}: FAIL -- $($r.Reason)" -ForegroundColor Red
    }
}

if (-not ($bSelectedOk -and $predictedProtectOk)) {
    Fail "at least one policy did not pass: b_selected=$bSelectedOk predicted_protect=$predictedProtectOk"
}

Write-Host ""
Write-Host "runtime: $AbsExe"
Write-Host "expert store: $AbsStore"
Write-Host "b_selected:        tokens=$($results['b_selected'].CompletionTokens) decisioni=$($results['b_selected'].DecisionCount) comparator=$($results['b_selected'].ComparatorResult)"
Write-Host "predicted_protect: tokens=$($results['predicted_protect'].CompletionTokens) decisioni=$($results['predicted_protect'].DecisionCount) comparator=$($results['predicted_protect'].ComparatorResult)"
Write-Host "R2-12 HOST CONFORMANCE: BOTH MATCH" -ForegroundColor Green
exit 0

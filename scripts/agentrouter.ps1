# Prepare the configs, show the plan, or run an explicitly selected paid test.
[CmdletBinding()]
param(
    [ValidateSet('Setup','Check','TryModels','Plan','Smoke','Full','Validate','Pilot','Diagnose')][string]$Action = 'Setup',
    [string]$Model = 'gpt-6-astra',
    [ValidateSet('sdk','qwen_code')][string]$Client = 'sdk',
    [switch]$ReuseProbe,
    [string]$Config,
    [ValidateSet('price_first','bounded_price_first','price_only')][string]$PromptProfile,
    [ValidateRange(16,8192)][int]$OutputTokens,
    [ValidateRange(1,60)][int]$RequestsPerMinute,
    [ValidateRange(128,1000000)][int]$TokensPerMinute,
    [double]$InputRate = -1,
    [double]$OutputRate = -1,
    [double]$SmokeBudget = 1,
    [ValidateRange(1,100)][int]$Rounds = 20,
    [double]$FullBudgetPerRun = 20,
    [ValidateRange(1,1000)][int]$Replicates = 1,
    [ValidateSet('https://co.agentrouter.org/v1','https://agentrouter.org/v1')]
    [string]$BaseUrl = 'https://agentrouter.org/v1',
    [ValidateSet('max_tokens','max_completion_tokens')]
    [string]$TokenParameter = 'max_completion_tokens'
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
$taskPreviousToken = $null
$taskTokenChanged = $false
$taskThreadVariables = @('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS')
$taskPreviousThreads = @{}
foreach ($taskName in $taskThreadVariables) {
    $taskPreviousThreads[$taskName] = [Environment]::GetEnvironmentVariable($taskName, 'Process')
}
Push-Location $taskRoot
try {
    # Keep the local math library from using too many threads while this launcher runs.
    foreach ($taskName in $taskThreadVariables) {
        [Environment]::SetEnvironmentVariable($taskName, '1', 'Process')
    }
    if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run scripts/setup.ps1 first to create .venv.' }
    if ($Client -eq 'qwen_code' -and $Action -eq 'Check') {
        & $taskPython -X utf8 scripts/check_qwen_code.py
        if ($LASTEXITCODE -ne 0) { throw 'Qwen Code failed local qualification. Paid requests remain blocked.' }
        return
    }
    if ($Action -in @('Validate','Pilot','Diagnose')) {
        if ($Client -ne 'qwen_code') { throw 'Validate, Pilot and Diagnose require -Client qwen_code.' }
        $taskConfig = if ($Config) { $Config } else { 'configs/local/agentrouter-qwen-smoke.json' }
        $taskArguments = @('-X', 'utf8', 'scripts/validate_qwen_live.py', $taskConfig)
        if ($PSBoundParameters.ContainsKey('Model')) { $taskArguments += @('--model', $Model) }
        if ($ReuseProbe) { $taskArguments += '--reuse-probe' }
        if ($PromptProfile) { $taskArguments += @('--prompt-profile', $PromptProfile) }
        if ($OutputTokens) { $taskArguments += @('--output-tokens', $OutputTokens) }
        if ($RequestsPerMinute) { $taskArguments += @('--requests-per-minute', $RequestsPerMinute) }
        if ($TokensPerMinute) { $taskArguments += @('--tokens-per-minute', $TokensPerMinute) }
        if ($Action -eq 'Diagnose') {
            $taskArguments += '--diagnostics'
        } elseif ($Action -eq 'Pilot') {
            $taskArguments += @('--pilot-rounds', $Rounds, '--replicates', $Replicates)
        }
        & $taskPython @taskArguments
        if ($LASTEXITCODE -ne 0) { throw 'Qwen execution stopped. Inspect the saved diagnostic evidence before retrying.' }
        return
    }
    if ($Client -eq 'qwen_code' -and $Action -in @('Smoke','Full')) {
        throw 'Use -Action Validate for the Qwen smoke test, or -Action Pilot for four arms. These actions enforce the shared allowance.'
    }
    if ($Client -eq 'qwen_code' -and $Action -eq 'TryModels') { throw 'Use Check first; model probing through this client is not qualified.' }
    if ($Action -eq 'Setup') {
        if ($InputRate -lt 0) { $InputRate = [double]::Parse((Read-Host 'Account INPUT price in USD per 1 million tokens'), [Globalization.CultureInfo]::InvariantCulture) }
        if ($OutputRate -lt 0) { $OutputRate = [double]::Parse((Read-Host 'Account OUTPUT price in USD per 1 million tokens'), [Globalization.CultureInfo]::InvariantCulture) }
        & $taskPython scripts/setup_agentrouter.py --client $Client --model $Model --input-rate $InputRate.ToString('R',[Globalization.CultureInfo]::InvariantCulture) --output-rate $OutputRate.ToString('R',[Globalization.CultureInfo]::InvariantCulture) --smoke-budget $SmokeBudget.ToString('R',[Globalization.CultureInfo]::InvariantCulture) --full-budget $FullBudgetPerRun.ToString('R',[Globalization.CultureInfo]::InvariantCulture) --base-url $BaseUrl --token-parameter $TokenParameter
        if ($LASTEXITCODE -ne 0) { throw 'Config preparation failed; no requests were made.' }
        if ($Client -eq 'qwen_code') { Write-Output 'Next: .\scripts\agentrouter.ps1 -Client qwen_code -Action Check' }
        else { Write-Output 'Next: .\scripts\agentrouter.ps1 -Action Smoke' }
        return
    }
    $taskConfig = if ($Action -in @('Check','TryModels','Smoke')) { 'configs/local/agentrouter-smoke.json' } else { 'configs/local/agentrouter-full.json' }
    if ($Client -eq 'qwen_code') { $taskConfig = $taskConfig.Replace('agentrouter-', 'agentrouter-qwen-') }
    if (-not (Test-Path -LiteralPath $taskConfig)) { throw 'Run .\scripts\agentrouter.ps1 -Action Setup first.' }
    $taskSettings = Get-Content -LiteralPath $taskConfig -Raw | ConvertFrom-Json
    if ($taskSettings.agent.backend -ne 'agentrouter') { throw 'This launcher requires the agentrouter backend.' }
    $taskRepeats = if ($Action -eq 'Smoke') { 1 } else { $Replicates }
    & $taskPython -m auditors_paradox validate-config $taskConfig --allow-paid
    if ($LASTEXITCODE -ne 0) { throw 'Config validation failed; no requests were made.' }
    if ($Action -notin @('Check','TryModels')) {
        & $taskPython -m auditors_paradox plan $taskConfig --replicates $taskRepeats
        if ($LASTEXITCODE -ne 0) { throw 'Planning failed; no requests were made.' }
        $taskTotalCap = [double]$taskSettings.budget.max_cost_usd * @($taskSettings.batch_arms).Count * $taskRepeats
        Write-Output ('Configured-rate batch allowance: USD ' + $taskTotalCap.ToString('0.00',[Globalization.CultureInfo]::InvariantCulture))
        Write-Output 'The application estimates cost; the gateway controls actual billing.'
        if ($Action -eq 'Plan') { return }
    }
    & $taskPython -c 'import openai'
    if ($LASTEXITCODE -ne 0) { throw 'Provider SDK missing: run uv sync --frozen --all-extras, then retry.' }
    if ($Client -eq 'qwen_code') {
        & $taskPython -X utf8 -c 'import sys; from pathlib import Path; from auditors_paradox.config import load_config; from auditors_paradox.adapters.qwen_code import QwenCodeBackend; QwenCodeBackend(load_config(Path(sys.argv[1])).agent, Path.cwd()).close()' $taskConfig
        if ($LASTEXITCODE -ne 0) { throw 'Qwen Code is not qualified. No token was requested and no inference was sent.' }
    }
    $taskPreviousToken = $env:AGENTROUTER_API_KEY
    $taskSecret = Read-Host 'PASTE your AgentRouter API token here, then press Enter (hidden)' -AsSecureString
    try {
        $env:AGENTROUTER_API_KEY = [Net.NetworkCredential]::new('', $taskSecret).Password
        $taskTokenChanged = $true
    } finally { $taskSecret.Dispose(); Remove-Variable taskSecret }
    if ([string]::IsNullOrWhiteSpace($env:AGENTROUTER_API_KEY)) { throw 'No API token was entered.' }
    if ($Action -eq 'TryModels') {
        Write-Output 'Up to three single requests, 256 output tokens each, USD 0.10 estimated reservation ceiling. Gateway billing applies.'
        & $taskPython -X utf8 scripts/try_agentrouter_models.py $taskConfig
        if ($LASTEXITCODE -ne 0) { throw 'No model returned visible text; inspect the diagnostic results above before retrying.' }
        return
    }
    if ($Action -eq 'Check') {
        & $taskPython scripts/check_agentrouter.py $taskConfig
        if ($LASTEXITCODE -ne 0) { throw 'Catalogue check failed; inspect the status above. No inference was requested.' }
        return
    }
    $taskId = 'router-' + $Action.ToLowerInvariant() + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,6)
    & $taskPython -m auditors_paradox sweep $taskConfig --replicates $taskRepeats --sweep-id $taskId --allow-paid --max-total-cost-usd $taskTotalCap.ToString('R',[Globalization.CultureInfo]::InvariantCulture)
    $taskRunExit = $LASTEXITCODE
    $taskSweep = Join-Path $taskRoot ('runs/' + $taskSettings.run_kind + '/' + $taskSettings.study_id + '/sweeps/' + $taskId + '/manifest.json')
    $taskAnalysis = Join-Path $taskRoot ('analysis/' + $taskId)
    if (Test-Path -LiteralPath $taskSweep) {
        $taskManifest = Get-Content -LiteralPath $taskSweep -Raw | ConvertFrom-Json
        if ($taskManifest.status -notin @('running','interrupted')) {
            & $taskPython -m auditors_paradox analyze $taskSweep --output $taskAnalysis
            if ($LASTEXITCODE -ne 0) { throw ('Analysis failed; saved sweep: ' + $taskSweep) }
            Write-Output ('Report: ' + (Join-Path $taskAnalysis 'report.md'))
            Write-Output ('Run table: ' + (Join-Path $taskAnalysis 'runs.csv'))
            if (Test-Path -LiteralPath (Join-Path $taskAnalysis 'rounds.csv')) { Write-Output ('Round table: ' + (Join-Path $taskAnalysis 'rounds.csv')) }
        }
    }
    if ($taskRunExit -ne 0) { throw ('Some experiments failed. Inspect the saved sweep before retrying: ' + $taskSweep) }
    $taskInputRateText = ([double]$taskSettings.budget.input_usd_per_million).ToString('R',[Globalization.CultureInfo]::InvariantCulture)
    $taskOutputRateText = ([double]$taskSettings.budget.output_usd_per_million).ToString('R',[Globalization.CultureInfo]::InvariantCulture)
    $taskPlannedRuns = @($taskSettings.batch_arms).Count * $taskRepeats
    & $taskPython -m auditors_paradox usage $taskSweep --output (Join-Path $taskAnalysis 'usage.json') --planned-runs $taskPlannedRuns --planned-rounds $taskSettings.rounds --input-rate $taskInputRateText --output-rate $taskOutputRateText
    if ($LASTEXITCODE -ne 0) { throw 'Runs completed but usage export failed; evidence is retained.' }
} finally {
    if ($taskTokenChanged) {
        if ($null -eq $taskPreviousToken) { Remove-Item Env:\AGENTROUTER_API_KEY -ErrorAction SilentlyContinue }
        else { $env:AGENTROUTER_API_KEY = $taskPreviousToken }
    }
    $taskPreviousToken = $null
    foreach ($taskName in $taskThreadVariables) {
        [Environment]::SetEnvironmentVariable($taskName, $taskPreviousThreads[$taskName], 'Process')
    }
    Pop-Location
}

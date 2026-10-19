# Run all the checks, lint, format, types, docs and doctor, then the tests with coverage.
param([string]$EvidenceDirectory = '.cache/checks')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location $taskRoot
try {
    $taskEvidence = [IO.Path]::GetFullPath((Join-Path $taskRoot $EvidenceDirectory))
    if (-not $taskEvidence.StartsWith($taskRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Evidence output must stay inside this project.' }
    New-Item -ItemType Directory -Path $taskEvidence -Force | Out-Null
    $taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
    foreach ($taskArgs in @(
        @('-m','ruff','check','src','tests','scripts'),
        @('-m','black','--check','--workers','1','src','tests','scripts'),
        @('-m','mypy','src/auditors_paradox'),
        @('scripts/check_docs.py'),
        @('-m','auditors_paradox','doctor')
    )) {
        & $taskPython @taskArgs
        if ($LASTEXITCODE -ne 0) { throw ('Check failed: ' + ($taskArgs -join ' ')) }
    }
    $taskTemp = [IO.Path]::GetFullPath((Join-Path $taskRoot ('.cache/test-' + [guid]::NewGuid().ToString('N'))))
    if (-not $taskTemp.StartsWith((Join-Path $taskRoot '.cache') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test directory.' }
    $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
    $env:MPLCONFIGDIR = Join-Path $taskRoot '.cache/matplotlib'
    & $taskPython -X utf8 -m pytest tests -q -p pytest_cov.plugin -p no:cacheprovider --basetemp $taskTemp --cov=auditors_paradox --cov-report term-missing --cov-report "json:$taskEvidence/coverage.json" --junitxml "$taskEvidence/pytest.xml"
    if ($LASTEXITCODE -ne 0) { throw 'Project tests or coverage failed.' }
} finally { Pop-Location }

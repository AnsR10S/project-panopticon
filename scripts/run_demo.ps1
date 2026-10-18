# Run the scripted four arm demo, verify every run, then analyze the sweep. No paid calls.
param([int]$Replicates = 3)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location $taskRoot
try {
    $taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
    $taskId = 'demo-v1-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,6)
    & $taskPython -m auditors_paradox sweep configs/pilot/scripted-four-arm.yaml --sweep-id $taskId --replicates $Replicates
    if ($LASTEXITCODE -ne 0) { throw 'Scripted demo did not complete every assigned run.' }
    $taskSweep = Join-Path $taskRoot ('runs/smoke/four-arm-engineering-v1/sweeps/' + $taskId + '/manifest.json')
    $taskManifest = Get-Content -LiteralPath $taskSweep -Raw | ConvertFrom-Json
    foreach ($taskRun in $taskManifest.runs) {
        & $taskPython -m auditors_paradox verify (Join-Path $taskRoot $taskRun.manifest_path)
        if ($LASTEXITCODE -ne 0) { throw 'Demo evidence verification failed.' }
    }
    & $taskPython -m auditors_paradox analyze $taskSweep --output (Join-Path $taskRoot ('analysis/' + $taskId))
    if ($LASTEXITCODE -ne 0) { throw 'Demo analysis failed.' }
    Write-Output ('Verified scripted demonstration: analysis/' + $taskId + '/report.md')
} finally { Pop-Location }

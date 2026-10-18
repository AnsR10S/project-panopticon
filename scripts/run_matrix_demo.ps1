# Run the expanded scripted matrix, all four auditor by horizon cells. No paid calls.
param([int]$Replicates = 2)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location $taskRoot
try {
    $taskId = 'matrix-v11-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,6)
    & (Join-Path $taskRoot '.venv/Scripts/python.exe') -X utf8 -m auditors_paradox matrix configs/pilot/expanded-scripted-matrix.yaml --matrix-id $taskId --replicates $Replicates
    if ($LASTEXITCODE -ne 0) { throw 'Expanded matrix did not complete all assigned runs.' }
    Write-Output ('Verified matrix analyses: analysis/' + $taskId)
} finally { Pop-Location }

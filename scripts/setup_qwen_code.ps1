# Install the pinned client locally without running package installation scripts.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $taskRoot 'tools/qwen-code')
try {
    & npm.cmd ci --ignore-scripts --omit=dev --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Pinned client installation failed.' }
    Write-Output 'Installed locally. Qualification is still required before paid use.'
} finally { Pop-Location }

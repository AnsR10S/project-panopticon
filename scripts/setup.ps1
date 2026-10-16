# First time setup, get the pinned upstream, install the locked dependencies and run doctor.
param()
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location $taskRoot
try {
    & (Join-Path $PSScriptRoot 'bootstrap_upstream.ps1')
    $env:UV_CACHE_DIR = Join-Path $taskRoot '.cache/uv'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $taskRoot '.cache/python'
    & uv sync --frozen --all-extras --python 3.11
    if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed.' }
    & '.\.venv\Scripts\python.exe' -m auditors_paradox doctor
    if ($LASTEXITCODE -ne 0) { throw 'Project preflight failed.' }
} finally { Pop-Location }

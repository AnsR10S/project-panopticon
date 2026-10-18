# Temporary smoke runner stub for initial pipeline verification
param(
    [string]$ConfigPath = "configs/pilot/scripted-fixed-signal.yaml"
)

Write-Host "Running smoke stub with config: $ConfigPath"
python -m auditors_paradox sweep $ConfigPath
if ($LASTEXITCODE -ne 0) {
    Write-Error "Smoke run failed."
    exit 1
}
Write-Host "Smoke run completed successfully."

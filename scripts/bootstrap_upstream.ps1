# Clone CollusionLab at the pinned commit from the vendored bundle (or fallback to remote) if not present, and verify clean checkout.
param()
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskLock = Get-Content -LiteralPath (Join-Path $taskRoot 'external/collusion-lab.lock.json') -Raw | ConvertFrom-Json
$taskCheckout = [IO.Path]::GetFullPath((Join-Path $taskRoot $taskLock.checkout_path))
if (-not $taskCheckout.StartsWith($taskRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Upstream checkout escaped workspace.' }
if (-not (Test-Path -LiteralPath $taskCheckout)) {
    $taskBundle = if ($taskLock.bundle_path) { [IO.Path]::GetFullPath((Join-Path $taskRoot $taskLock.bundle_path)) } else { $null }
    if ($taskBundle -and (Test-Path -LiteralPath $taskBundle)) {
        if ($taskLock.bundle_sha256) {
            $taskActualHash = (Get-FileHash -LiteralPath $taskBundle -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($taskActualHash -ne $taskLock.bundle_sha256.ToLowerInvariant()) {
                throw 'Vendored snapshot bundle hash does not match lock checksum.'
            }
        }
        & git clone --no-recurse-submodules $taskBundle $taskCheckout
        if ($LASTEXITCODE -ne 0) { throw 'Clone from local snapshot bundle failed; partial checkout retained for inspection.' }
    } else {
        & git clone --no-recurse-submodules $taskLock.repository $taskCheckout
        if ($LASTEXITCODE -ne 0) { throw 'Clone failed; partial checkout retained for inspection.' }
    }
    & git -C $taskCheckout checkout --detach $taskLock.commit
    if ($LASTEXITCODE -ne 0) { throw 'Cannot check out the pinned revision.' }
}
$taskRevision = & git -C $taskCheckout rev-parse HEAD
$taskDirty = & git -C $taskCheckout status --porcelain
if ($LASTEXITCODE -ne 0 -or $taskRevision -ne $taskLock.commit -or $taskDirty) {
    throw 'Existing upstream checkout differs from the pin or is dirty. It has been preserved.'
}
Write-Output "Verified upstream $taskRevision"

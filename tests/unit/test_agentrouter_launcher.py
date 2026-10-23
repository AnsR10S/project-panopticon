"""Check the launcher's temporary thread limits without contacting a provider."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_launcher_limits_threads_and_restores_environment(tmp_path, failure, existing):
    """Child commands get one thread; success and failure both restore the caller."""
    shell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell is required for launcher integration")
    root = Path(__file__).resolve().parents[2]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    launcher = scripts / "agentrouter.ps1"
    launcher.write_text(
        (root / "scripts/agentrouter.ps1")
        .read_text(encoding="utf-8")
        .replace(".venv/Scripts/python.exe", "fake-python.ps1"),
        encoding="utf-8",
    )
    (tmp_path / "fake-python.ps1").write_text(
        "if ($env:OPENBLAS_NUM_THREADS -ne '1' -or $env:OMP_NUM_THREADS -ne '1') "
        "{ throw 'Child did not receive the thread limits' }\n"
        "Add-Content -LiteralPath (Join-Path $PSScriptRoot 'children.txt') -Value 'limited'\n"
        f"$global:LASTEXITCODE = {7 if failure else 0}\n",
        encoding="utf-8",
    )
    configs = tmp_path / "configs/local"
    configs.mkdir(parents=True)
    (configs / "agentrouter-full.json").write_text(
        json.dumps(
            {
                "agent": {"backend": "agentrouter"},
                "batch_arms": ["silent"],
                "budget": {"max_cost_usd": 1},
            }
        ),
        encoding="utf-8",
    )
    initial = "'8'" if existing else "$null"
    harness = tmp_path / "check.ps1"
    harness.write_text(
        f"$env:OPENBLAS_NUM_THREADS = {initial}\n"
        f"$env:OMP_NUM_THREADS = {initial}\n"
        "$caught = $false\n"
        "try { & (Join-Path $PSScriptRoot 'scripts/agentrouter.ps1') -Action Plan }\n"
        "catch {\n"
        "  if ($_.Exception.Message -notlike 'Config validation failed*') { throw }\n"
        "  $caught = $true\n"
        "}\n"
        f"if ($caught -ne ${str(failure).lower()}) {{ throw 'Unexpected exit path' }}\n"
        f"if ($env:OPENBLAS_NUM_THREADS -ne {initial} -or $env:OMP_NUM_THREADS -ne {initial}) "
        "{ throw 'Previous environment was not restored' }\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (tmp_path / "children.txt").read_text().splitlines()
    assert calls == ["limited"] * (1 if failure else 2)


@pytest.mark.parametrize("optimized", [False, True])
def test_qwen_launcher_forwards_selected_model_and_reuses_probe(tmp_path, optimized):
    """Keep explicit model choices on the shared allowance path."""
    shell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell is required for launcher integration")
    root = Path(__file__).resolve().parents[2]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    launcher = scripts / "agentrouter.ps1"
    launcher.write_text(
        (root / "scripts/agentrouter.ps1")
        .read_text(encoding="utf-8")
        .replace(".venv/Scripts/python.exe", "fake-python.ps1"),
        encoding="utf-8",
    )
    (tmp_path / "fake-python.ps1").write_text(
        "$args | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'arguments.txt')\n$global:LASTEXITCODE = 0\n",
        encoding="utf-8",
    )
    arguments = [
        shell,
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(launcher),
        "-Client",
        "qwen_code",
        "-Action",
        "Validate",
        "-Model",
        "gpt-5.6-sol",
        "-ReuseProbe",
    ]
    if optimized:
        arguments += [
            "-Config",
            "configs/local/agentrouter-qwen-price-only.json",
            "-PromptProfile",
            "price_only",
            "-OutputTokens",
            "4096",
            "-RequestsPerMinute",
            "1",
            "-TokensPerMinute",
            "20000",
        ]
    result = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    arguments = (tmp_path / "arguments.txt").read_text().splitlines()
    assert "scripts/validate_qwen_live.py" in arguments
    if optimized:
        assert "configs/local/agentrouter-qwen-price-only.json" in arguments
        assert arguments[-8:] == [
            "--prompt-profile",
            "price_only",
            "--output-tokens",
            "4096",
            "--requests-per-minute",
            "1",
            "--tokens-per-minute",
            "20000",
        ]
    else:
        assert arguments[-3:] == ["--model", "gpt-5.6-sol", "--reuse-probe"]

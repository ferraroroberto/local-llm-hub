# Local pre-ship verification gate.
#
# Runs the same checks the CI workflow runs, so a green local run
# means a green CI run (modulo runner OS quirks). Use before pushing
# to `main` or before opening a PR.
#
# Steps:
#   1. byte-compile every .py file under src/, app_web/, tray/, scripts/
#   2. `pytest -q` (unit tests, no GPU / no real CLIs)
#   3. `pytest tests/e2e -q --browser chromium`
#
# Stops on the first failure.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "❌ .venv\Scripts\python.exe not found at $Python" -ForegroundColor Red
    exit 1
}

function Step($label, $block) {
    Write-Host ""
    Write-Host "▶ $label" -ForegroundColor Cyan
    & $block
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ $label failed (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host "✅ $label" -ForegroundColor Green
}

Step "byte-compile" {
    & $Python -m compileall -q src app_web tray scripts
}

Step "pytest (unit)" {
    & $Python -m pytest -q --ignore=tests/e2e
}

Step "pytest (e2e · chromium)" {
    & $Python -m pytest tests/e2e -q --browser chromium
}

Write-Host ""
Write-Host "🚀 all green" -ForegroundColor Green

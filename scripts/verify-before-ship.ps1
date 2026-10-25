# Local pre-ship verification gate.
#
# Runs the same checks the CI workflow runs, so a green local run
# means a green CI run (modulo runner OS quirks). Use before pushing
# to `main` or before opening a PR.
#
# Steps:
#   1. byte-compile every .py file under src/, app_web/, tray/, scripts/
#   2. static ratchet: no new duplicate block (jscpd via npx) or unused
#      import/variable (ruff F401,F841) or likely bug/security pattern (curated
#      ruff B/S set, S113 strict) beyond .static-ratchet-baseline.json.
#      Whole tree, never routed; exit 3 (a detector could not run) is NOT
#      CONFIRMED and fails, never a pass or a skip.
#   3. docs ratchet: no new issue reference, dated heading or changelog
#      heading in README.md or docs/**/*.md, and a README no larger than its
#      baseline, beyond .docs-ratchet-baseline.json. Whole tree, never
#      routed; exit 3 (a doc could not be read) is NOT CONFIRMED and fails.
#   4. comment ratchet: no file's count of `#NNN` issue references inside
#      source comments rises beyond .comment-ratchet-baseline.json. Whole
#      tree, never routed; exit 3 (a file could not be scanned) fails.
#   5. structure ratchet: no new file over 1,000 lines, function over 100
#      lines or directory over 40 modules, and no baselined one growing,
#      beyond .structure-ratchet-baseline.json; the root folders must match
#      the `service` shape plus extra_roots. Whole tree, never routed; exit 3
#      (git unusable, an unparseable file, an unknown shape) fails.
#   6. instruction ratchet: no CLAUDE.md / AGENTS.md / skill file gains a prompt-audit
#      lint-rule hit or passes a size cap beyond .instruction-ratchet-baseline.json.
#      Whole tree, never routed; exit 3 (could not measure) fails.
#   7. doc-refs ratchet: no README.md / CLAUDE.md / docs/**/*.md reference to a repo
#      path, module, relative link or loopback port that does not exist, beyond
#      .doc-refs-ratchet-baseline.json. Whole tree, never routed; exit 3 (could not
#      measure) fails.
#   8. dead-code ratchet: no Python function, class or method that nothing in the repo
#      refers to, beyond .dead-code-ratchet-baseline.json. Whole tree, never routed
#      (deleting the last caller strands a definition elsewhere); exit 3 (git
#      unusable, a file that does not parse) fails.
#   9. conventions ratchet: no file's hit count for an enabled rule (no-window,
#      logging-not-print, no-drive-path) rises beyond .conventions-ratchet-baseline.json.
#      Whole tree, never routed; exit 3 (git unusable, a file that does not parse)
#      fails.
#   10. `pytest -q` (unit tests, no GPU / no real CLIs)
#   11. e2e, routed by the diff (scripts/e2e_route.py wraps classify_e2e.py and
#      the .fleet.toml [e2e] rules): nothing for a docs/backend-only diff, a
#      smoke target or one surface's slice for a narrow one, otherwise
#      `pytest tests/e2e -q --browser chromium --junitxml=data/e2e-junit.xml`.
#      Anything the router cannot read runs the full suite, never less.
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

Step "static ratchet" {
    # The wrapper maps the script's exit 3 to NOT CONFIRMED
    # (tests/ratchets/test_static_ratchet_gate.py pins it); call it, never
    # scripts/static_ratchet.py directly.
    & $Python -m scripts.static_ratchet_gate
}

Step "docs ratchet" {
    # Same shape: the wrapper maps exit 3 (a doc could not be read) to NOT
    # CONFIRMED (tests/ratchets/test_docs_ratchet_gate.py pins it); call it, never
    # scripts/docs_ratchet.py directly.
    & $Python -m scripts.docs_ratchet_gate
}

Step "comment ratchet" {
    # No gate wrapper needed (unlike static/docs ratchet): exit 3 (a file
    # could not be scanned) already propagates as a failure here, same as
    # exit 1/2 — never a pass or a skip.
    & $Python scripts/comment_ratchet.py
}

Step "structure ratchet" {
    # Same as the comment ratchet: Step fails on any non-zero exit, so exit 3
    # (git unusable, an unparseable file, an unknown shape) is a failure here,
    # never a pass or a skip.
    & $Python scripts/structure_ratchet.py
}

Step "instruction ratchet" {
    # Same as the comment and structure ratchets: Step fails on any non-zero
    # exit, so exit 3 (could not measure) is a failure here, never a pass or a
    # skip.
    & $Python scripts/instruction_ratchet.py
}

Step "doc-refs ratchet" {
    # Same as the instruction ratchet: Step fails on any non-zero exit, so
    # exit 3 (could not measure) is a failure here, never a pass or a skip.
    & $Python scripts/doc_refs_ratchet.py
}

Step "dead-code ratchet" {
    # Same as the doc-refs ratchet: Step fails on any non-zero exit, so
    # exit 3 (could not measure) is a failure here, never a pass or a skip.
    & $Python scripts/dead_code_ratchet.py
}

Step "conventions ratchet" {
    # Same as the dead-code ratchet: Step fails on any non-zero exit, so exit 3
    # (git unusable, a file that does not parse) is a failure here, never a pass
    # or a skip.
    & $Python scripts/conventions_ratchet.py
}

Step "pytest (unit)" {
    & $Python -m pytest -q --ignore=tests/e2e
}

# Diff-proportionate e2e: the router prints E2E_* lines and owns the
# fail-safe (scripts/e2e_route.py). The only doubt left here is the wrapper
# itself dying before it prints a tier, which also runs the whole suite.
$tier = "full"; $e2eTarget = "tests/e2e"; $e2eBrowsers = "chromium"
$routeReason = "e2e router gave no verdict -- running the full suite (fail-safe)"
$routeOut = & $Python -m scripts.e2e_route
$routeExit = $LASTEXITCODE
$kv = @{}
foreach ($line in $routeOut) {
    if ($line -match '^(E2E_[A-Z_]+)=(.*)$') { $kv[$matches[1]] = $matches[2] }
}
if ($routeExit -eq 0 -and $kv["E2E_TIER"]) {
    $tier = $kv["E2E_TIER"]
    $e2eTarget = $kv["E2E_PYTEST_TARGET"]
    $e2eBrowsers = $kv["E2E_BROWSERS"]
    $routeReason = $kv["E2E_REASON"]
}

if ($tier -eq "skip") {
    Write-Host ""
    Write-Host "▶ pytest (e2e) · skipped, diff touches no e2e surface" -ForegroundColor Cyan
    Write-Host "   reason: $routeReason" -ForegroundColor DarkGray
    Write-Host "✅ pytest (e2e) skipped" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "e2e routing: $tier" -ForegroundColor Cyan
    Write-Host "   reason: $routeReason" -ForegroundColor DarkGray
    # A surface slice over several modules arrives space-joined; pytest needs
    # each as its own argument.
    $e2eArgs = @($e2eTarget -split '\s+' | Where-Object { $_ })
    foreach ($b in ($e2eBrowsers -split ',' | Where-Object { $_ })) {
        $e2eArgs += @("--browser", $b)
    }
    Step "pytest (e2e · ${tier}: $e2eTarget · $e2eBrowsers)" {
        # JUnit record of the run: /e2e-audit reads per-test seconds from it
        # ([e2e] junit_xml in .fleet.toml). Git-ignored, overwritten each run.
        & $Python -m pytest @e2eArgs -q --junitxml=data/e2e-junit.xml
    }
}

Write-Host ""
Write-Host "🚀 all green" -ForegroundColor Green

param(
    [ValidateSet('All', '1', '2', '3', '4', '5', '6', '7', '8', '9')]
    [string]$Phase = 'All',
    [switch]$StartServices,
    [switch]$BuildServices,
    [switch]$RunDriftDemo,
    [string]$Python = 'python',
    [string]$Tofu = 'tofu',
    [string]$ReportPath
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pytestBaseTemp = Join-Path $repoRoot ('.pytest-tmp-' + [guid]::NewGuid().ToString('N'))
$frontendPath = Join-Path $repoRoot 'frontend'
$checks = [System.Collections.Generic.List[object]]::new()
if ($Python -eq 'python' -and (Test-Path -LiteralPath (Join-Path $repoRoot '.venv\Scripts\python.exe'))) {
    $Python = Join-Path $repoRoot '.venv\Scripts\python.exe'
}

function Add-Check([string]$Name, [string]$Status, [string]$Details, [string]$Fix) {
    $checks.Add([pscustomobject]@{
        name = $Name
        status = $Status
        details = $Details.Trim()
        suggested_fix = $Fix
    })
    $color = switch ($Status) {
        'PASS' { 'Green' }
        'FAIL' { 'Red' }
        default { 'Yellow' }
    }
    Write-Host "[$Status] $Name" -ForegroundColor $color
    if (-not [string]::IsNullOrWhiteSpace($Details)) { Write-Host $Details }
    if ($Status -ne 'PASS' -and -not [string]::IsNullOrWhiteSpace($Fix)) {
        Write-Host "  Fix: $Fix" -ForegroundColor DarkYellow
    }
}

function Invoke-CommandCheck([string]$Name, [string]$Executable, [string[]]$Arguments, [string]$WorkDir, [string]$Fix) {
    if (-not (Get-Command $Executable -ErrorAction SilentlyContinue)) {
        Add-Check $Name 'SKIP' "Executable not found: $Executable" $Fix
        return
    }
    Push-Location $WorkDir
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5 treats native stderr as a terminating error when
        # ErrorActionPreference is Stop. Continue temporarily so Docker's full
        # build output is captured and the native exit code can be reported.
        $ErrorActionPreference = 'Continue'
        $output = @(& $Executable @Arguments 2>&1 | ForEach-Object { "$_" })
        $exitCode = $LASTEXITCODE
    } catch {
        Add-Check $Name 'FAIL' $_.Exception.Message $Fix
        return
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
        Pop-Location
    }
    $details = ($output | Select-Object -Last 40) -join [Environment]::NewLine
    if ($exitCode -eq 0) {
        Add-Check $Name 'PASS' $details ''
    } else {
        Add-Check $Name 'FAIL' "Exit code: $exitCode`n$details" $Fix
    }
}

function Test-HttpEndpoint([string]$Name, [string]$Url, [string]$ExpectedProperty, [string]$ExpectedValue) {
    try {
        $response = Invoke-RestMethod -Uri $Url -TimeoutSec 4
        if ($response.$ExpectedProperty -ne $ExpectedValue) {
            Add-Check $Name 'FAIL' "Unexpected response from $Url" 'Check backend logs with: docker compose logs --tail 100 backend'
            return
        }
        Add-Check $Name 'PASS' "$Url returned $ExpectedProperty=$ExpectedValue" ''
    } catch {
        Add-Check $Name 'FAIL' "$Url is unreachable or returned an invalid response: $($_.Exception.Message)" 'Start the stack with .\scripts\start.ps1, then inspect: docker compose ps and docker compose logs --tail 100 backend'
    }
}

Write-Host "DriftWatch verification | Phase: $Phase" -ForegroundColor Cyan
Write-Host "Repository: $repoRoot`n"

$runPhase1 = $Phase -in @('All', '1', '4')
$runPhase2 = $Phase -in @('All', '2')
$runPhase3 = $Phase -in @('All', '3')
$runPhase5 = $Phase -in @('All', '5')
$runPhase6 = $Phase -in @('All', '6')
$runPhase7 = $Phase -in @('All', '7')
$runPhase8 = $Phase -in @('All', '8')
$runPhase9 = $Phase -in @('All', '9')

if ($runPhase1) {
    Write-Host '--- Phase 1: foundation and runtime ---' -ForegroundColor Cyan
    Invoke-CommandCheck 'Foundation API tests' $Python @('-m', 'pytest', '-p', 'no:cacheprovider', '--basetemp', $pytestBaseTemp, 'backend/tests/test_health.py') $repoRoot 'Review the error details. If dependencies are missing, run python -m pip install -r backend/requirements-dev.txt.'
    Invoke-CommandCheck 'Backend lint' $Python @('-m', 'ruff', 'check', 'backend') $repoRoot 'Install dev dependencies and fix Ruff findings.'

    $npm = Get-Command 'npm' -ErrorAction SilentlyContinue
    if (-not $npm) {
        Add-Check 'Frontend lint/build' 'SKIP' 'npm was not found.' 'Install Node.js LTS, then run npm install from frontend/.'
    } elseif (-not (Test-Path (Join-Path $frontendPath 'node_modules'))) {
        Add-Check 'Frontend lint/build' 'SKIP' 'frontend/node_modules is missing.' 'Run npm install from frontend/, then rerun this script.'
    } else {
        Invoke-CommandCheck 'Frontend lint' 'npm' @('run', 'lint') $frontendPath 'Run npm install from frontend/, then fix the reported ESLint errors.'
        Invoke-CommandCheck 'Frontend build' 'npm' @('run', 'build') $frontendPath 'Run npm install from frontend/, then fix the reported TypeScript/Vite errors.'
    }

    $docker = Get-Command 'docker' -ErrorAction SilentlyContinue
    if (-not $docker) {
        Add-Check 'Compose configuration' 'SKIP' 'Docker CLI was not found.' 'Install Docker Desktop or Docker Engine with the Compose plugin.'
    } else {
        Invoke-CommandCheck 'Compose configuration' 'docker' @('compose', 'config', '--quiet') $repoRoot 'Check docker-compose.yml and required .env values.'
        if ($StartServices) {
            if ($BuildServices) {
                Invoke-CommandCheck 'Compose startup' 'docker' @('compose', 'up', '--build', '-d') $repoRoot 'Review the preceding Docker error, then inspect: docker compose logs --tail 100'
            } else {
                Invoke-CommandCheck 'Compose startup' 'docker' @('compose', 'up', '-d') $repoRoot 'Review the preceding Docker error, then inspect: docker compose logs --tail 100'
            }
        }
        $composeRunning = $false
        try {
            $composeStatus = @(& docker compose ps --format json 2>&1 | ForEach-Object { "$_" })
            $composeExitCode = $LASTEXITCODE
            $composeRunning = $composeExitCode -eq 0 -and (($composeStatus -join "`n") -match '"State"\s*:\s*"running"|"state"\s*:\s*"running"')
            if ($composeRunning) {
                Add-Check 'Compose containers' 'PASS' 'At least one Compose service is running.' ''
            } else {
                Add-Check 'Compose containers' 'SKIP' 'No running Compose services detected; runtime smoke checks require the stack.' 'Start it with .\scripts\start.ps1 or rerun with -StartServices.'
            }
        } catch {
            Add-Check 'Compose containers' 'SKIP' "Could not inspect Docker services: $($_.Exception.Message)" 'Check Docker Desktop is running and run docker compose ps in a terminal.'
        }
    }

    if ($composeRunning) {
        Test-HttpEndpoint 'Backend liveness' 'http://localhost:8000/health/live' 'status' 'ok'
        Test-HttpEndpoint 'Backend readiness' 'http://localhost:8000/health/ready' 'status' 'ready'
        try {
            $null = Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:5173' -TimeoutSec 4
            Add-Check 'Frontend HTTP smoke' 'PASS' 'http://localhost:5173 returned a response.' ''
        } catch {
            Add-Check 'Frontend HTTP smoke' 'FAIL' "http://localhost:5173 is unreachable: $($_.Exception.Message)" 'Inspect: docker compose ps and docker compose logs --tail 100 frontend'
        }
    } else {
        Add-Check 'Backend HTTP smoke' 'SKIP' 'Compose stack is not running.' 'Start the stack with .\scripts\start.ps1 or rerun with -StartServices.'
        Add-Check 'Frontend HTTP smoke' 'SKIP' 'Compose stack is not running.' 'Start the stack with .\scripts\start.ps1 or rerun with -StartServices.'
    }
}

if ($runPhase2) {
    Write-Host "`n--- Phase 2: drift engine ---" -ForegroundColor Cyan
    Invoke-CommandCheck 'Drift engine tests' $Python @('-m', 'pytest', '-p', 'no:cacheprovider', '--basetemp', $pytestBaseTemp, 'backend/tests/test_drift_adapter.py', 'backend/tests/test_drift_parser.py') $repoRoot 'Review the error details. If dependencies are missing, run python -m pip install -r backend/requirements-dev.txt.'
    Invoke-CommandCheck 'Drift engine lint' $Python @('-m', 'ruff', 'check', 'backend/src/driftwatch/drift', 'backend/src/driftwatch/scan_cli.py', 'backend/tests/test_drift_adapter.py', 'backend/tests/test_drift_parser.py') $repoRoot 'Fix the reported Ruff findings.'
    if ($RunDriftDemo) {
        $currentPowerShell = (Get-Process -Id $PID).Path
        Invoke-CommandCheck 'Read-only scan demo' $currentPowerShell @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'demo.ps1'), '-Python', $Python, '-Tofu', $Tofu) $repoRoot 'Install OpenTofu and Python dependencies. The demo needs to initialize and apply its local baseline once.'
    } else {
        Add-Check 'Read-only scan demo' 'SKIP' 'Not requested; the demo runs tofu apply to establish its local baseline.' 'Run with -RunDriftDemo when you want the full clean → drift → clean scan demonstration.'
    }
}

if ($runPhase3) {
    Write-Host "`n--- Phase 3: database and API ---" -ForegroundColor Cyan
    Invoke-CommandCheck 'Versioned API tests' $Python @('-m', 'pytest', '-p', 'no:cacheprovider', '--basetemp', $pytestBaseTemp, 'backend/tests/test_api_v1.py') $repoRoot 'Review the failure details; install backend requirements if modules are missing.'
    Invoke-CommandCheck 'Phase 3 lint' $Python @('-m', 'ruff', 'check', 'backend/src/driftwatch/database.py', 'backend/src/driftwatch/db_models.py', 'backend/src/driftwatch/schemas.py', 'backend/src/driftwatch/api_v1.py', 'backend/migrations', 'backend/tests/test_api_v1.py') $repoRoot 'Fix the reported lint issues.'
}

if ($runPhase5) {
    Write-Host "`n--- Phase 5: scan automation ---" -ForegroundColor Cyan
    Invoke-CommandCheck 'Automation tests' $Python @('-m', 'pytest', '-p', 'no:cacheprovider', '--basetemp', $pytestBaseTemp, 'backend/tests/test_automation.py', 'backend/tests/test_api_v1.py') $repoRoot 'Review the failure details; install backend requirements if modules are missing.'
    Invoke-CommandCheck 'Phase 5 lint' $Python @('-m', 'ruff', 'check', 'backend/src/driftwatch/config.py', 'backend/src/driftwatch/scan_service.py', 'backend/src/driftwatch/worker.py', 'backend/src/driftwatch/scheduler.py', 'backend/src/driftwatch/api_v1.py', 'backend/tests/test_automation.py', 'backend/tests/test_api_v1.py') $repoRoot 'Fix the reported Ruff findings.'
    Invoke-CommandCheck 'Compose configuration' 'docker' @('compose', 'config', '--quiet') $repoRoot 'Check docker-compose.yml and required .env values.'
}

if ($runPhase6) {
    Write-Host "`n--- Phase 6: GitHub integration ---" -ForegroundColor Cyan
    Invoke-CommandCheck 'GitHub integration tests' $Python @('-m', 'pytest', '-p', 'no:cacheprovider', '--basetemp', $pytestBaseTemp, 'backend/tests/test_github.py', 'backend/tests/test_api_v1.py') $repoRoot 'Review the failure details; install backend requirements if modules are missing.'
    Invoke-CommandCheck 'Phase 6 lint' $Python @('-m', 'ruff', 'check', 'backend/src/driftwatch/config.py', 'backend/src/driftwatch/github.py', 'backend/src/driftwatch/schemas.py', 'backend/src/driftwatch/api_v1.py', 'backend/tests/test_github.py', 'backend/tests/test_api_v1.py') $repoRoot 'Fix the reported Ruff findings.'
    Invoke-CommandCheck 'Compose configuration' 'docker' @('compose', 'config', '--quiet') $repoRoot 'Check docker-compose.yml and required .env values.'
}

if ($runPhase7) {
    Write-Host "`n--- Phase 7: Kubernetes adapter ---" -ForegroundColor Cyan
    Invoke-CommandCheck 'Kubernetes adapter tests' $Python @('-m', 'pytest', '-p', 'no:cacheprovider', '--basetemp', $pytestBaseTemp, 'backend/tests/test_kubernetes_adapter.py', 'backend/tests/test_automation.py', 'backend/tests/test_api_v1.py') $repoRoot 'Review the failure details; install backend requirements if modules are missing.'
    Invoke-CommandCheck 'Phase 7 lint' $Python @('-m', 'ruff', 'check', 'backend/src/driftwatch/config.py', 'backend/src/driftwatch/drift/adapters/kubernetes.py', 'backend/src/driftwatch/scan_service.py', 'backend/src/driftwatch/scheduler.py', 'backend/src/driftwatch/api_v1.py', 'backend/tests/test_kubernetes_adapter.py', 'backend/tests/test_automation.py', 'backend/tests/test_api_v1.py') $repoRoot 'Fix the reported Ruff findings.'
    Invoke-CommandCheck 'Compose configuration' 'docker' @('compose', 'config', '--quiet') $repoRoot 'Check docker-compose.yml and required .env values.'
}

if ($runPhase8) {
    Write-Host "`n--- Phase 8: security and production hardening ---" -ForegroundColor Cyan
    Invoke-CommandCheck 'Authentication and authorization tests' $Python @('-m', 'pytest', '-p', 'no:cacheprovider', '--basetemp', $pytestBaseTemp, 'backend/tests/test_auth.py', 'backend/tests/test_rate_limit.py', 'backend/tests/test_api_v1.py', 'backend/tests/test_github.py') $repoRoot 'Review the failure details and install backend requirements if modules are missing.'
    Invoke-CommandCheck 'Phase 8 lint' $Python @('-m', 'ruff', 'check', 'backend/src/driftwatch/auth.py', 'backend/src/driftwatch/auth_api.py', 'backend/src/driftwatch/config.py', 'backend/src/driftwatch/main.py', 'backend/src/driftwatch/rate_limit.py', 'backend/src/driftwatch/api_v1.py', 'backend/tests/test_auth.py', 'backend/tests/test_rate_limit.py', 'backend/tests/test_api_v1.py', 'backend/tests/test_github.py') $repoRoot 'Fix the reported Ruff findings.'
    Invoke-CommandCheck 'Compose configuration' 'docker' @('compose', 'config', '--quiet') $repoRoot 'Check docker-compose.yml and required .env values.'
    $npm = Get-Command 'npm' -ErrorAction SilentlyContinue
    if ($npm -and (Test-Path (Join-Path $frontendPath 'node_modules'))) {
        Invoke-CommandCheck 'Frontend authentication build' 'npm' @('run', 'build') $frontendPath 'Run npm install from frontend/, then fix the reported TypeScript/Vite errors.'
    } else {
        Add-Check 'Frontend authentication build' 'SKIP' 'npm or frontend/node_modules is unavailable.' 'Install Node.js and run npm install from frontend/.'
    }
}

if ($runPhase9) {
    Write-Host "`n--- Phase 9: in-app notifications ---" -ForegroundColor Cyan
    Invoke-CommandCheck 'Notification API and lifecycle tests' $Python @('-m', 'pytest', '-p', 'no:cacheprovider', '--basetemp', $pytestBaseTemp, 'backend/tests/test_automation.py', 'backend/tests/test_api_v1.py') $repoRoot 'Review the failure details and install backend requirements if modules are missing.'
    Invoke-CommandCheck 'Phase 9 lint' $Python @('-m', 'ruff', 'check', 'backend/src/driftwatch/scan_service.py', 'backend/src/driftwatch/api_v1.py', 'backend/src/driftwatch/schemas.py', 'backend/tests/test_automation.py', 'backend/tests/test_api_v1.py') $repoRoot 'Fix the reported Ruff findings.'
    Invoke-CommandCheck 'Compose configuration' 'docker' @('compose', 'config', '--quiet') $repoRoot 'Check docker-compose.yml and required .env values.'
    $npm = Get-Command 'npm' -ErrorAction SilentlyContinue
    if ($npm -and (Test-Path (Join-Path $frontendPath 'node_modules'))) {
        Invoke-CommandCheck 'Frontend notifications lint' 'npm' @('run', 'lint') $frontendPath 'Fix the reported ESLint findings.'
        Invoke-CommandCheck 'Frontend notifications build' 'npm' @('run', 'build') $frontendPath 'Fix the reported TypeScript/Vite errors.'
    } else {
        Add-Check 'Frontend notifications checks' 'SKIP' 'npm or frontend/node_modules is unavailable.' 'Install Node.js and run npm install from frontend/.'
    }
}

$failed = @($checks | Where-Object status -eq 'FAIL').Count
$skipped = @($checks | Where-Object status -eq 'SKIP').Count
$summary = [pscustomobject]@{
    timestamp = (Get-Date).ToString('o')
    repository = $repoRoot
    phase = $Phase
    result = if ($failed -eq 0) { 'PASS' } else { 'FAIL' }
    totals = [pscustomobject]@{ passed = @($checks | Where-Object status -eq 'PASS').Count; failed = $failed; skipped = $skipped }
    checks = @($checks)
}

if ($ReportPath) {
    $reportFullPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $ReportPath))
    $reportDir = Split-Path -Parent $reportFullPath
    if (-not (Test-Path -LiteralPath $reportDir)) { New-Item -ItemType Directory -Path $reportDir -Force | Out-Null }
    $summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportFullPath -Encoding UTF8
    Write-Host "`nJSON report: $reportFullPath"
}

Write-Host "`nResult: $($summary.result) | Passed: $($summary.totals.passed) | Failed: $failed | Skipped: $skipped" -ForegroundColor $(if ($failed) { 'Red' } else { 'Green' })
if ($failed -gt 0) { exit 1 }

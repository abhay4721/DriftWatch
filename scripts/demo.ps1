param(
    [string]$Python = 'python',
    [string]$Tofu = 'tofu'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$project = Join-Path $repoRoot 'demo\tofu-local'
$generated = Join-Path $project 'generated\app.conf'
$env:DRIFTWATCH_SOURCE_ROOT = Join-Path $repoRoot 'demo'
$env:DRIFTWATCH_TOFU_PATH = $Tofu
$env:PYTHONPATH = Join-Path $repoRoot 'backend\src'

if (-not (Get-Command $Tofu -ErrorAction SilentlyContinue) -and -not (Test-Path -LiteralPath $Tofu)) {
    throw "OpenTofu executable '$Tofu' was not found. Install tofu and retry."
}

function Invoke-Scan([int]$ExpectedExitCode, [string]$Label) {
    Write-Host "`n=== $Label ===" -ForegroundColor Cyan
    $stateFile = Join-Path $project 'terraform.tfstate'
    $stateHashBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $stateFile).Hash
    Push-Location $repoRoot
    try {
        & $Python -m driftwatch.scan_cli --source tofu-local --tofu $Tofu
        $actualExitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($actualExitCode -ne $ExpectedExitCode) {
        throw "Scan '$Label' exited with $actualExitCode; expected $ExpectedExitCode."
    }
    $stateHashAfter = (Get-FileHash -Algorithm SHA256 -LiteralPath $stateFile).Hash
    if ($stateHashAfter -ne $stateHashBefore) {
        throw "Scan '$Label' changed the OpenTofu state file."
    }
}

Push-Location $project
try {
    Write-Host 'Initializing the local OpenTofu project...' -ForegroundColor Cyan
    & $Tofu init -input=false -no-color
    if ($LASTEXITCODE -ne 0) { throw 'tofu init failed.' }

    Write-Host 'Applying the approved baseline configuration...' -ForegroundColor Cyan
    & $Tofu apply -input=false -auto-approve -no-color
    if ($LASTEXITCODE -ne 0) { throw 'tofu apply failed while creating the demo baseline.' }
} finally {
    Pop-Location
}

Invoke-Scan 0 'Initial clean scan'
try {
    Write-Host "`nSimulating an out-of-band edit to generated/app.conf..." -ForegroundColor Yellow
    [System.IO.File]::WriteAllText($generated, "APP_ENV=development`n", [System.Text.UTF8Encoding]::new($false))
    Invoke-Scan 2 'External change detected'
} finally {
    if (Test-Path -LiteralPath $generated) {
        Write-Host "`nRestoring generated/app.conf to the approved value..." -ForegroundColor Yellow
        [System.IO.File]::WriteAllText($generated, "APP_ENV=production`n", [System.Text.UTF8Encoding]::new($false))
    }
}
Invoke-Scan 0 'Drift resolved'
Write-Host "`nDemo complete. The last scan is clean; no apply ran during detection." -ForegroundColor Green

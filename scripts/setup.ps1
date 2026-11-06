$ErrorActionPreference = 'Stop'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI is required. Install Docker Desktop or Docker Engine first.'
}

if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host 'Created .env from .env.example. Review the local development credentials.'
}

docker compose config --quiet
Write-Host 'DriftWatch Compose configuration is valid.'

param([switch]$RemoveData)

$ErrorActionPreference = 'Stop'
if ($RemoveData) {
    docker compose down --volumes --remove-orphans
    Write-Host 'Containers and local database/metrics volumes removed.'
} else {
    docker compose down --remove-orphans
    Write-Host 'Containers stopped. Persistent local volumes were kept.'
}

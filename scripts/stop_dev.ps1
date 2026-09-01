[CmdletBinding()]
param(
    [switch]$KeepInfrastructure
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (Get-Command git -ErrorAction SilentlyContinue) {
    $gitRoot = & git -C $projectRoot rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and $gitRoot) {
        $projectRoot = $gitRoot.Trim()
    }
}
$composeFile = Join-Path $projectRoot "docker-compose.p0.yml"
$pidFile = Join-Path $projectRoot "logs\dev-processes.json"

if (Test-Path -LiteralPath $pidFile) {
    $processes = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
    foreach ($process in $processes) {
        if (Get-Process -Id $process.pid -ErrorAction SilentlyContinue) {
            & taskkill.exe /PID $process.pid /T /F | Out-Null
            Write-Host "Stopped $($process.name) (PID $($process.pid))."
        }
    }
    Remove-Item -LiteralPath $pidFile -Force
}

if (-not $KeepInfrastructure) {
    Set-Location $projectRoot
    & docker compose -f $composeFile --profile graph down
}

Write-Host "Development services stopped."

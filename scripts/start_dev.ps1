[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [switch]$EnableGraph
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot "frontend"
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$composeFile = Join-Path $projectRoot "docker-compose.p0.yml"
$logDir = Join-Path $projectRoot "logs"
$pidFile = Join-Path $logDir "dev-processes.json"

function Assert-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command is unavailable: $Name"
    }
}

function Invoke-Checked([string]$Description, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

function Assert-Python312([string]$PythonPath) {
    $versionText = & $PythonPath -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to inspect Python version at $PythonPath"
    }
    $version = [version]$versionText.Trim()
    if ($version -lt [version]"3.12.0") {
        throw "Python 3.12+ is required, but $PythonPath is $version. Rename or remove .venv, then rerun this script."
    }
}

Set-Location $projectRoot
Assert-Command "docker"
Assert-Command "npm.cmd"

if (Test-Path -LiteralPath $pidFile) {
    $savedProcesses = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
    $running = @($savedProcesses |
        Where-Object { Get-Process -Id $_.pid -ErrorAction SilentlyContinue }
    )
    if ($running.Count -gt 0) {
        throw "Development services are already running. Run scripts\stop_dev.ps1 first."
    }
    Remove-Item -LiteralPath $pidFile -Force
}

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".env"))) {
    Copy-Item -LiteralPath (Join-Path $projectRoot ".env.example") -Destination (Join-Path $projectRoot ".env")
    Write-Host "Created .env from .env.example. Add DASHSCOPE_API_KEY to enable model responses."
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    if ($SkipInstall) {
        throw ".venv is missing and -SkipInstall was supplied."
    }
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Invoke-Checked "Create Python virtual environment" {
            & uv venv --python 3.12 (Join-Path $projectRoot ".venv")
        }
    }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        Invoke-Checked "Create Python virtual environment" {
            & py -3.12 -m venv (Join-Path $projectRoot ".venv")
        }
    }
    else {
        Assert-Command "python"
        Invoke-Checked "Create Python virtual environment" {
            & python -m venv (Join-Path $projectRoot ".venv")
        }
    }
}
Assert-Python312 $venvPython

if (-not $SkipInstall) {
    Invoke-Checked "Bootstrap pip" {
        & $venvPython -m ensurepip --upgrade
    }
    Invoke-Checked "Upgrade pip" {
        & $venvPython -m pip install --upgrade pip
    }
    Invoke-Checked "Install Python dependencies" {
        & $venvPython -m pip install -r (Join-Path $projectRoot "requirements.txt")
    }
    if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot "node_modules"))) {
        Invoke-Checked "Install frontend dependencies" {
            & npm.cmd --prefix $frontendRoot ci
        }
    }
}

$composeArgs = @("compose", "-f", $composeFile)
if ($EnableGraph) {
    $composeArgs += @("--profile", "graph")
}
$composeArgs += @("up", "-d", "--wait")
Invoke-Checked "Start Docker services" {
    & docker @composeArgs
}
if (-not $EnableGraph) {
    & docker compose -f $composeFile --profile graph stop neo4j | Out-Null
}
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# The process environment takes precedence over .env so the application and
# the selected Compose profile always agree for this launch.
$env:NEO4J_ENABLED = $(if ($EnableGraph) { "true" } else { "false" })

$backend = Start-Process -FilePath $venvPython `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logDir "dev-backend.out.log") `
    -RedirectStandardError (Join-Path $logDir "dev-backend.err.log")

$worker = Start-Process -FilePath $venvPython `
    -ArgumentList @("-m", "workers.runner") `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logDir "dev-worker.out.log") `
    -RedirectStandardError (Join-Path $logDir "dev-worker.err.log")

$frontend = Start-Process -FilePath "npm.cmd" `
    -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1") `
    -WorkingDirectory $frontendRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logDir "dev-frontend.out.log") `
    -RedirectStandardError (Join-Path $logDir "dev-frontend.err.log")

@(
    [PSCustomObject]@{ name = "backend"; pid = $backend.Id }
    [PSCustomObject]@{ name = "worker"; pid = $worker.Id }
    [PSCustomObject]@{ name = "frontend"; pid = $frontend.Id }
) | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $pidFile

Write-Host "Development services started."
Write-Host "Frontend: http://127.0.0.1:5173"
Write-Host "API docs: http://127.0.0.1:8000/docs"
Write-Host "Neo4j:   $($(if ($EnableGraph) { 'http://127.0.0.1:17474' } else { 'disabled (use -EnableGraph)' }))"
Write-Host "Logs:     $logDir"
Write-Host "Stop:     powershell -ExecutionPolicy Bypass -File .\scripts\stop_dev.ps1"

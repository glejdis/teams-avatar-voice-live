<#
.SYNOPSIS
    Bootstrap local Python venvs for the three Python apps in this repo.

.DESCRIPTION
    For each of:
      - launcher/         (CLI for scheduling meetings + dispatching the bot)
      - hosted-agent/     (the Foundry agent container — runs locally for dev)
      - browser-fallback/ (Flask app for the ACS WebRTC fallback path)

    this script will:
      1. Create a `.venv` (if missing) using the global `python` on PATH.
      2. `pip install` requirements (editable for launcher via pyproject.toml,
         requirements.txt for the other two).
      3. Copy the `.env.example` to `.env` if no `.env` exists yet.

    The Teams bot in `bot/` is a .NET project — bootstrap it with
    `dotnet restore` from `bot/src/EchoBot/`. The VMSS-side hot patches in
    `scripts/vmss/` are for already-deployed instances and need no local
    venv.

.EXAMPLE
    pwsh ./scripts/bootstrap-dev.ps1

.EXAMPLE
    pwsh ./scripts/bootstrap-dev.ps1 -App hosted-agent
#>
[CmdletBinding()]
param(
    [ValidateSet('all', 'launcher', 'hosted-agent', 'browser-fallback')]
    [string]$App = 'all'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')

$apps = @(
    @{ Name = 'launcher';         Path = '.';                  Editable = $true;  Run = 'python -m launcher --help' },
    @{ Name = 'hosted-agent';     Path = 'hosted-agent';       Editable = $false; Run = 'python main.py    # http://localhost:8088' },
    @{ Name = 'browser-fallback'; Path = 'browser-fallback';   Editable = $false; Run = 'python app.py     # http://localhost:3000' }
)

if ($App -ne 'all') { $apps = $apps | Where-Object { $_.Name -eq $App } }

$python = (Get-Command python -ErrorAction SilentlyContinue)?.Source
if (-not $python) { throw 'python not found on PATH. Install Python 3.11+ first.' }

foreach ($a in $apps) {
    $appDir = Resolve-Path (Join-Path $repoRoot $a.Path)
    if (-not (Test-Path $appDir)) {
        Write-Warning "Skipping $($a.Name) - directory not found: $appDir"
        continue
    }

    Write-Host ""
    Write-Host "=== $($a.Name) ===" -ForegroundColor Cyan

    Push-Location $appDir
    try {
        $venv = Join-Path $appDir '.venv'
        if (-not (Test-Path $venv)) {
            Write-Host "  Creating .venv ..." -ForegroundColor Yellow
            & $python -m venv .venv
        } else {
            Write-Host "  .venv already exists" -ForegroundColor Green
        }

        $venvPython = Join-Path $venv 'Scripts\python.exe'
        if (-not (Test-Path $venvPython)) { $venvPython = Join-Path $venv 'bin/python' }

        & $venvPython -m pip install --quiet --upgrade pip

        if ($a.Editable -and (Test-Path 'pyproject.toml')) {
            Write-Host "  Installing project editable (pyproject.toml) ..." -ForegroundColor Yellow
            & $venvPython -m pip install --quiet -e '.[dev]' 2>$null
            if ($LASTEXITCODE -ne 0) {
                # No [dev] extra defined — fall back to plain editable install.
                & $venvPython -m pip install --quiet -e .
            }
        } elseif (Test-Path 'requirements.txt') {
            Write-Host "  Installing requirements.txt ..." -ForegroundColor Yellow
            & $venvPython -m pip install --quiet -r requirements.txt
        } else {
            Write-Warning "  No pyproject.toml or requirements.txt found - skipping dependency install"
        }

        $envFile = Join-Path $appDir '.env'
        $envTemplate = Join-Path $appDir '.env.example'
        if (-not (Test-Path $envFile)) {
            if (Test-Path $envTemplate) {
                Copy-Item $envTemplate $envFile
                Write-Host "  Copied .env.example -> .env (EDIT IT before running)" -ForegroundColor Yellow
            } else {
                Write-Warning "  No .env.example found - .env not created"
            }
        } else {
            Write-Host "  .env already exists" -ForegroundColor Green
        }

        Write-Host "  Ready. To run:" -ForegroundColor Green
        Write-Host "    cd $($a.Path)"
        Write-Host "    .venv\Scripts\Activate.ps1"
        Write-Host "    az login   # if you haven't already"
        Write-Host "    $($a.Run)"
    } finally {
        Pop-Location
    }
}

Write-Host ""
Write-Host "All done. Next: fill in each app's .env, then 'az login' and run." -ForegroundColor Cyan

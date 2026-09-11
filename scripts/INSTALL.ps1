#Requires -Version 5.1
<#
.SYNOPSIS
    Install AI Creative Studio
.DESCRIPTION
    Creates the Python virtual environment, installs backend and frontend dependencies and seeds .env files.
    Part of AI Creative Studio. Run from the repository root or anywhere.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
param([switch]$GPU)
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$VenvPython = Join-Path $Backend '.venv\Scripts\python.exe'

function Write-Step { param([string]$Message) Write-Host "`n==> $Message" -ForegroundColor Cyan }
function Write-Ok { param([string]$Message) Write-Host "    $Message" -ForegroundColor Green }
function Ensure-Venv {
    if (-not (Test-Path $VenvPython)) {
        throw "Backend virtual environment is missing. Run .\scripts\INSTALL.ps1 first."
    }
}

Write-Step "Checking Python"
# NOTE: the '??' null-coalescing operator is not available in Windows PowerShell 5.1,
# so resolve the interpreter with plain if-statements. Also fall back to the Windows
# 'py' launcher, which the python.org installer adds even without "Add to PATH".
$python = Get-Command python  -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $python) { $python = Get-Command py      -ErrorAction SilentlyContinue }
if (-not $python) { throw "Python 3.11+ is required. Install it from https://www.python.org/downloads/" }
Write-Ok ("Using " + (& $python.Source --version))

Write-Step "Creating backend virtual environment"
if (-not (Test-Path (Join-Path $Backend '.venv'))) { & $python.Source -m venv (Join-Path $Backend '.venv') }
Write-Ok "venv ready"

Write-Step "Installing backend dependencies"
& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPython -m pip install -r (Join-Path $Backend 'requirements.txt')
if ($GPU) {
    Write-Step "Installing GPU extras"
    & $VenvPython -m pip install -r (Join-Path $Backend 'requirements-gpu.txt')
}
Write-Ok "backend dependencies installed"

Write-Step "Installing frontend dependencies"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "Node.js 20+ (with npm) is required for the web app." }
Push-Location $Frontend
try { npm install --no-audit --no-fund } finally { Pop-Location }
Write-Ok "frontend dependencies installed"

Write-Step "Preparing configuration"
if (-not (Test-Path (Join-Path $Backend '.env'))) {
    Copy-Item (Join-Path $Backend '.env.example') (Join-Path $Backend '.env')
    Write-Ok "created backend/.env from the example file"
} else { Write-Ok "backend/.env already exists" }
if (-not (Test-Path (Join-Path $Frontend '.env'))) {
    Copy-Item (Join-Path $Frontend '.env.example') (Join-Path $Frontend '.env')
    Write-Ok "created frontend/.env from the example file"
} else { Write-Ok "frontend/.env already exists" }

Write-Host ""
Write-Host "Installation complete." -ForegroundColor Green
Write-Host "Next: .\scripts\START.ps1  (then open http://localhost:5173)"
Write-Host "Sign in with the bootstrap admin shown in backend/.env (change it immediately)."

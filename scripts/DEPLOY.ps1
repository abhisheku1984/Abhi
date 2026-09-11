#Requires -Version 5.1
<#
.SYNOPSIS
    Production deployment
.DESCRIPTION
    Builds the frontend and starts the API in production mode.
    Part of AI Creative Studio. Run from the repository root or anywhere.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
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

Ensure-Venv
Write-Step "Building frontend"
Push-Location $Frontend
try { npm run build } finally { Pop-Location }
Write-Step "Starting production server on http://0.0.0.0:8000"
Write-Host "Press Ctrl+C to stop." -ForegroundColor Yellow
& $VenvPython -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2

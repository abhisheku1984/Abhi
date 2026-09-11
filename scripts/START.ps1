#Requires -Version 5.1
<#
.SYNOPSIS
    Start AI Creative Studio
.DESCRIPTION
    Starts the API (port 8000) and the web app (port 5173).
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

Write-Step "Starting backend API on http://localhost:8000"
Start-Process -FilePath $VenvPython -ArgumentList '-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000' `
    -WorkingDirectory $Backend -WindowStyle Minimized
Start-Sleep -Seconds 3

Write-Step "Starting web app on http://localhost:5173"
Start-Process -FilePath 'npm' -ArgumentList 'run','dev' -WorkingDirectory $Frontend -WindowStyle Minimized

Start-Sleep -Seconds 4
Write-Host ""
Write-Host "AI Creative Studio is starting up:" -ForegroundColor Green
Write-Host "  Web app      http://localhost:5173"
Write-Host "  API docs     http://localhost:8000/api/docs"
Write-Host "  Health       http://localhost:8000/api/health"
Write-Host ""
Write-Host "Sign in with the bootstrap admin (see backend/.env) and change the password immediately."

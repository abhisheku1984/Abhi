#Requires -Version 5.1
<#
.SYNOPSIS
    Run the test suites
.DESCRIPTION
    Runs backend pytest and frontend vitest.
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
Write-Step "Backend tests"
& $VenvPython -m pytest (Join-Path $Backend 'tests') -q
Write-Step "Frontend tests"
Push-Location $Frontend
try { npm run test -- --run } finally { Pop-Location }
Write-Host "All tests passed." -ForegroundColor Green

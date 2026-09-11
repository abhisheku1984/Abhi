#Requires -Version 5.1
<#
.SYNOPSIS
    Check runtime health
.DESCRIPTION
    Probes API, database, queue, models, GPU and storage.
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
& $VenvPython (Join-Path $Root 'scripts\healthcheck.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

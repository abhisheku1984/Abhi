#Requires -Version 5.1
<#
.SYNOPSIS
    Back up the studio
.DESCRIPTION
    Archives the database, assets, configuration and logs.
    Part of AI Creative Studio. Run from the repository root or anywhere.
#>
[CmdletBinding()]
param([string]$Out = 'backups')

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

Write-Step "Creating backup"
& $VenvPython (Join-Path $Root 'scripts\backup.py') $Out
Write-Host "Backup complete." -ForegroundColor Green

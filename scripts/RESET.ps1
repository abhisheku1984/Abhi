#Requires -Version 5.1
<#
.SYNOPSIS
    Reset local data
.DESCRIPTION
    Deletes the database, generated assets and caches. Backups are not touched.
    Part of AI Creative Studio. Run from the repository root or anywhere.
#>
[CmdletBinding()]
param([switch]$Force)

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

if (-not $Force) {
    $answer = Read-Host 'This deletes the database and every generated asset. Type RESET to continue'
    if ($answer -ne 'RESET') { Write-Host 'Cancelled.'; exit 0 }
}
Write-Step "Removing local state"
Remove-Item -Recurse -Force (Join-Path $Backend 'data\studio.db') -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $Backend 'storage\*') -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $Backend 'data\jobs') -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $Frontend 'dist') -ErrorAction SilentlyContinue
Write-Host "Local data reset. Start the platform to create a fresh admin account." -ForegroundColor Green

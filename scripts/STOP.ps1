#Requires -Version 5.1
<#
.SYNOPSIS
    Stop AI Creative Studio
.DESCRIPTION
    Stops the API and the web app.
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

Write-Step "Stopping AI Creative Studio"
Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'node.exe'" |
    Where-Object { $_.CommandLine -like '*uvicorn app.main:app*' -or $_.CommandLine -like '*vite*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Ok ("stopped PID " + $_.ProcessId) }
Write-Host "Done." -ForegroundColor Green

#Requires -Version 5.1
<#
.SYNOPSIS
    Install GPU extras
.DESCRIPTION
    Installs torch/diffusers so neural models can run on an NVIDIA GPU.
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
Write-Step "Installing GPU requirements (this is a large download)"
& $VenvPython -m pip install -r (Join-Path $Backend 'requirements-gpu.txt')
Write-Host ""
Write-Host "GPU extras installed. Restart the platform, then confirm in Model Manager." -ForegroundColor Green
Write-Host "No weights are downloaded automatically - install models from Model Manager when you are ready."

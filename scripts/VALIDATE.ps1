#Requires -Version 5.1
<#
.SYNOPSIS
    Validate the installation
.DESCRIPTION
    Runs environment checks, tests, build and (optionally) a live smoke test.
    Part of AI Creative Studio. Run from the repository root or anywhere.
#>
[CmdletBinding()]
param([switch]$SkipTests, [switch]$SkipBuild, [switch]$WithServer)

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

$arguments = @((Join-Path $Root 'scripts\validate.py'))
if ($SkipTests) { $arguments += '--skip-tests' }
if ($SkipBuild) { $arguments += '--skip-build' }
if ($WithServer) { $arguments += '--with-server' }
& $VenvPython @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

<#
.SYNOPSIS
  Run the Label Studio school edition locally (SQLite + local files, development only).

.DESCRIPTION
  Applies migrations, collects static files when needed, optionally creates the demo colleges and
  accounts (-Seed), then starts the development server.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy\school\local\run-local.ps1 -Seed
  Then open http://127.0.0.1:8080/ (demo password: Demo@2026) and http://127.0.0.1:8080/school/
#>
param(
    [string]$VenvDir,
    [string]$DataDir,
    [string]$Address = '127.0.0.1:8080',
    [switch]$Seed
)
$ErrorActionPreference = 'Stop'

function Normalize-Path([string]$Path) {
    $full = [System.IO.Path]::GetFullPath($Path)
    if ($full -match '^[a-z]:') { $full = $full.Substring(0, 1).ToUpper() + $full.Substring(1) }
    return $full
}

$RepoRoot = Normalize-Path (Join-Path $PSScriptRoot '..\..\..')
$Workspace = Split-Path $RepoRoot -Parent
if (-not $VenvDir) { $VenvDir = Join-Path $Workspace '.venv' }
if (-not $DataDir) { $DataDir = Join-Path $Workspace 'local-data' }
$Python = Join-Path (Normalize-Path $VenvDir) 'Scripts\python.exe'
if (-not (Test-Path $Python)) { throw "Virtual environment not found: run setup-local.ps1 first ($Python)" }

$env:LABEL_STUDIO_BASE_DATA_DIR = Normalize-Path $DataDir
$env:DJANGO_SETTINGS_MODULE = 'core.settings.school_local'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location (Join-Path $RepoRoot 'label_studio')

Write-Host "==> Data directory: $env:LABEL_STUDIO_BASE_DATA_DIR"
& $Python manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw 'Migrations failed' }
& $Python manage.py collectstatic --noinput
if ($LASTEXITCODE -ne 0) { throw 'collectstatic failed' }
if ($Seed) {
    & $Python manage.py school_seed_demo
    if ($LASTEXITCODE -ne 0) { throw 'Seeding demo data failed' }
}

Write-Host "==> Label Studio (school edition): http://$Address/   College workbench: http://$Address/school/"
& $Python manage.py runserver $Address --noreload

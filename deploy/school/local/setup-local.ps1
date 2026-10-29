<#
.SYNOPSIS
  Prepare a local Windows development environment for the Label Studio school edition.

.DESCRIPTION
  - Creates a Python 3.12 virtual environment (default: <workspace>\.venv, next to the repository).
  - Installs the dependencies of the official label-studio wheel matching this source tree with pip
    (uv and bun may be blocked by group policy, so neither is required).
  - Builds the customized front end from this checkout with Node/npm and the repository's webpack target.
  - Makes the venv import label_studio from this checkout (.pth file + minimal package metadata).
  Local data (SQLite + uploaded files) goes to <workspace>\local-data; see run-local.ps1.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy\school\local\setup-local.ps1 -WithTests
#>
param(
    [string]$VenvDir,
    [string]$LabelStudioVersion = '1.23.2',
    [switch]$WithTests
)
$ErrorActionPreference = 'Stop'

function Normalize-Path([string]$Path) {
    # pytest registers conftest plugins by path string; a lower-case drive letter registers them twice
    $full = [System.IO.Path]::GetFullPath($Path)
    if ($full -match '^[a-z]:') { $full = $full.Substring(0, 1).ToUpper() + $full.Substring(1) }
    return $full
}

$RepoRoot = Normalize-Path (Join-Path $PSScriptRoot '..\..\..')
$Workspace = Split-Path $RepoRoot -Parent
if (-not $VenvDir) { $VenvDir = Join-Path $Workspace '.venv' }
$VenvDir = Normalize-Path $VenvDir
$Python = Join-Path $VenvDir 'Scripts\python.exe'

if (-not (Test-Path $Python)) {
    Write-Host "==> Creating virtual environment $VenvDir"
    & py -3.12 -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required (py -3.12)' }
}

Write-Host "==> Installing label-studio $LabelStudioVersion dependencies"
& $Python -m pip install --quiet --upgrade pip
$WheelDir = Join-Path $env:TEMP "label-studio-wheel-$LabelStudioVersion"
New-Item -ItemType Directory -Force $WheelDir | Out-Null
& $Python -m pip download --quiet --no-deps -d $WheelDir "label-studio==$LabelStudioVersion"
if ($LASTEXITCODE -ne 0) { throw 'Could not download the label-studio wheel' }
$Wheel = Get-ChildItem $WheelDir -Filter "label_studio-$LabelStudioVersion-*.whl" | Select-Object -First 1
& $Python -m pip install --quiet $Wheel.FullName
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
# Keep the dependencies, drop the packaged copy so the checkout is imported instead
& $Python -m pip uninstall --quiet -y label-studio

$Script = @'
import pathlib, site, sys
wheel, repo, version = sys.argv[1], pathlib.Path(sys.argv[2]), sys.argv[3]
packages = pathlib.Path(site.getsitepackages()[-1])
(packages / 'label_studio_src.pth').write_text(str(repo) + '\n')
info = packages / f'label_studio-{version}.dist-info'
info.mkdir(exist_ok=True)
(info / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: label-studio\nVersion: {version}\n')
(info / 'RECORD').write_text('')
print(f'source path: {repo}')
'@
$Script | & $Python - $Wheel.FullName $RepoRoot $LabelStudioVersion
if ($LASTEXITCODE -ne 0) { throw 'Source environment setup failed' }

if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js is required to build the school frontend' }
Push-Location (Join-Path $RepoRoot 'web')
try {
    if (-not (Test-Path 'node_modules\nx\bin\nx.js')) {
        # This webpack target uses JavaScript/Babel; do not execute optional package binaries.
        & npm.cmd install --ignore-scripts --legacy-peer-deps --package-lock=false --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
    }
    $env:NX_DAEMON = 'false'
    $env:BUILD_NO_SERVER = 'true'
    & node node_modules\nx\bin\nx.js build labelstudio --configuration=development
    if ($LASTEXITCODE -ne 0) { throw 'School frontend build failed' }
} finally {
    Pop-Location
}

if ($WithTests) {
    Write-Host '==> Installing test dependencies'
    & $Python -m pip install --quiet 'pytest==7.2.2' 'pytest-django==4.9.0' 'pytest-env==0.6.2' 'pytest-mock==3.14.0' 'moto[s3]'
    if ($LASTEXITCODE -ne 0) { throw 'Test dependency installation failed' }
}

Write-Host "==> Done. Start with: powershell -ExecutionPolicy Bypass -File deploy\school\local\run-local.ps1 -Seed"

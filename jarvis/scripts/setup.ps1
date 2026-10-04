# JARVIS — one-time setup on Windows.
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "JARVIS setup — $root" -ForegroundColor Cyan

# Python 3.12+ (prefer the py launcher)
$python = $null
foreach ($candidate in @("py -3.13", "py -3.12", "python")) {
    $exe, $arg = $candidate.Split(" ", 2)
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        $version = & $exe $arg -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and [version]$version -ge [version]"3.12") { $python = $candidate; break }
    }
}
if (-not $python) { throw "Python 3.12+ is required (https://www.python.org/downloads/)." }
Write-Host "Using Python: $python"

if (-not (Test-Path "backend\.venv")) {
    $exe, $arg = $python.Split(" ", 2)
    & $exe $arg -m venv backend\.venv
}
& backend\.venv\Scripts\python.exe -m pip install --upgrade pip
& backend\.venv\Scripts\python.exe -m pip install -e "backend[dev]"

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "Node.js 20+ is required (https://nodejs.org)." }
npm install --no-save
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }

Write-Host "`nDone. Start JARVIS with:  npm run dev   (or scripts\dev.ps1)" -ForegroundColor Green

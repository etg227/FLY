$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

py -3 -m pip show pyinstaller *> $null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Installing PyInstaller..."
  py -3 -m pip install pyinstaller
  if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller failed." }
}

py -3 -m PyInstaller --onefile --noconsole --name launcher launcher.py
if ($LASTEXITCODE -ne 0) { throw "launcher build failed." }
py -3 -m PyInstaller --onefile --noconsole --name FLY start_fly.py
if ($LASTEXITCODE -ne 0) { throw "FLY build failed." }

Write-Host ""
Write-Host "Built: $ProjectRoot\dist\launcher.exe (install & update)" -ForegroundColor Green
Write-Host "Built: $ProjectRoot\dist\FLY.exe (daily direct start)" -ForegroundColor Green
Write-Host "Upload both to a GitHub Release, e.g.:"
Write-Host "  gh release create vX.Y.Z dist\launcher.exe dist\FLY.exe --title ""FLY vX.Y.Z"""
Read-Host "Press Enter to close"

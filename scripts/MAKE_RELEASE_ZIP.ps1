$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$OutZip = Join-Path (Split-Path -Parent $ProjectRoot) "FLY-update.zip"
$OutSha = Join-Path (Split-Path -Parent $ProjectRoot) "FLY-update.zip.sha256"
$Staging = Join-Path $env:TEMP ("fly-release-" + [guid]::NewGuid().ToString("N"))

$Include = @("main.py", "launcher.py", "README.md", "LICENSE", "VERSION",
             "START_FLY_DEBUG.bat", ".gitignore")
$IncludeDirs = @("backend", "rules", "scripts")

New-Item -ItemType Directory -Force -Path $Staging | Out-Null
foreach ($f in $Include) {
  $src = Join-Path $ProjectRoot $f
  if (Test-Path $src) { Copy-Item $src (Join-Path $Staging $f) }
}
foreach ($d in $IncludeDirs) {
  $src = Join-Path $ProjectRoot $d
  if (Test-Path $src) { Copy-Item $src (Join-Path $Staging $d) -Recurse }
}

Get-ChildItem -Path $Staging -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

if (Test-Path $OutZip) { Remove-Item $OutZip -Force }
if (Test-Path $OutSha) { Remove-Item $OutSha -Force }
Compress-Archive -Path (Join-Path $Staging "*") -DestinationPath $OutZip
Remove-Item -Recurse -Force $Staging

$Hash = (Get-FileHash -Path $OutZip -Algorithm SHA256).Hash.ToLowerInvariant()
"$Hash  FLY-update.zip" | Set-Content -Path $OutSha -Encoding ascii

Write-Host "Created: $OutZip" -ForegroundColor Green
Write-Host "Created: $OutSha" -ForegroundColor Green
Write-Host "private/, runtime/ and core/ are intentionally excluded."
Read-Host "Press Enter to close"

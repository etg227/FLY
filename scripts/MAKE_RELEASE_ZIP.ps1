$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Name = Split-Path -Leaf $ProjectRoot
$Staging = Join-Path $env:TEMP ("fly-release-" + [guid]::NewGuid().ToString("N"))
$OutZip = Join-Path (Split-Path -Parent $ProjectRoot) ($Name + "_release.zip")

# Only ship code + rules. NEVER ship private/ (subscription URL & credentials),
# runtime/ (downloaded subscription cache, browser profiles) or the 60MB core.
$Include = @("main.py", "README_CN.md", "START_FLY.bat", "INSTALL_CORE.bat", ".gitignore")
$IncludeDirs = @("backend", "rules", "scripts")

New-Item -ItemType Directory -Force -Path $Staging | Out-Null
foreach ($f in $Include) {
  $src = Join-Path $ProjectRoot $f
  if (Test-Path $src) { Copy-Item $src (Join-Path $Staging $f) }
}
foreach ($d in $IncludeDirs) {
  $src = Join-Path $ProjectRoot $d
  if (Test-Path $src) {
    Copy-Item $src (Join-Path $Staging $d) -Recurse
  }
}
Get-ChildItem -Path $Staging -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

if (Test-Path $OutZip) { Remove-Item $OutZip -Force }
Compress-Archive -Path (Join-Path $Staging "*") -DestinationPath $OutZip
Remove-Item -Recurse -Force $Staging

Write-Host "Release zip created: $OutZip" -ForegroundColor Green
Write-Host "(private/, runtime/ and core/mihomo.exe are intentionally NOT included.)"
Read-Host "Press Enter to close"

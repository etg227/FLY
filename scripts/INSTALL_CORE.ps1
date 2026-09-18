$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$CoreDir = Join-Path $ProjectRoot "core"
$TempDir = Join-Path $ProjectRoot "runtime\core-install"
New-Item -ItemType Directory -Force -Path $CoreDir | Out-Null
Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

$Headers = @{ "User-Agent"="FLY-Core-Installer"; "Accept"="application/vnd.github+json" }
$Release = Invoke-RestMethod -Uri "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest" -Headers $Headers
$Asset = $Release.assets | Where-Object { $_.name -match '^mihomo-windows-amd64-v1-v[0-9].*\.zip$' } | Select-Object -First 1
if (-not $Asset) {
  $Asset = $Release.assets | Where-Object { $_.name -match '^mihomo-windows-amd64.*\.zip$' } | Select-Object -First 1
}
if (-not $Asset) { throw "No Windows AMD64 Mihomo asset found." }

$ZipPath = Join-Path $TempDir $Asset.name
& curl.exe -L --fail --retry 3 -A "FLY-Core-Installer" -o $ZipPath $Asset.browser_download_url
if ($LASTEXITCODE -ne 0) { throw "curl download failed." }

Expand-Archive -Path $ZipPath -DestinationPath $TempDir -Force
$Exe = Get-ChildItem -Path $TempDir -Recurse -Filter "*.exe" | Where-Object { $_.Name -match '^mihomo.*\.exe$' } | Select-Object -First 1
if (-not $Exe) { throw "mihomo.exe not found in archive." }
Copy-Item -Force $Exe.FullName (Join-Path $CoreDir "mihomo.exe")
Write-Host "Installed successfully." -ForegroundColor Green
& (Join-Path $CoreDir "mihomo.exe") -v
Read-Host "Press Enter to close"

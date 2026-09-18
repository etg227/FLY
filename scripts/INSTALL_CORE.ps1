$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$CoreDir = Join-Path $ProjectRoot "core"
$TempDir = Join-Path $ProjectRoot "runtime\core-install"
New-Item -ItemType Directory -Force -Path $CoreDir | Out-Null
Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

# Mainland networks often cannot reach GitHub directly; retry via mirrors.
$Mirrors = @("", "https://ghproxy.net/", "https://gh-proxy.com/")
$Headers = @{ "User-Agent"="FLY-Core-Installer"; "Accept"="application/vnd.github+json" }

$Release = $null
foreach ($m in $Mirrors) {
  try {
    $Release = Invoke-RestMethod -Uri ($m + "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest") -Headers $Headers -TimeoutSec 20
    break
  } catch {
    Write-Host "Fetching release info failed ($(if ($m) { $m } else { 'direct' })): $($_.Exception.Message)"
  }
}
if (-not $Release) { throw "Cannot reach GitHub (direct or mirrors). Check your network." }

$Asset = $Release.assets | Where-Object { $_.name -match '^mihomo-windows-amd64-v1-v[0-9].*\.zip$' } | Select-Object -First 1
if (-not $Asset) {
  $Asset = $Release.assets | Where-Object { $_.name -match '^mihomo-windows-amd64.*\.zip$' } | Select-Object -First 1
}
if (-not $Asset) { throw "No Windows AMD64 Mihomo asset found." }

$ZipPath = Join-Path $TempDir $Asset.name
$Downloaded = $false
foreach ($m in $Mirrors) {
  & curl.exe -L --fail --retry 2 -A "FLY-Core-Installer" -o $ZipPath ($m + $Asset.browser_download_url)
  if ($LASTEXITCODE -eq 0) { $Downloaded = $true; break }
  Write-Host "Download failed ($(if ($m) { $m } else { 'direct' })), trying next mirror..."
}
if (-not $Downloaded) { throw "Download failed via all mirrors." }

Expand-Archive -Path $ZipPath -DestinationPath $TempDir -Force
$Exe = Get-ChildItem -Path $TempDir -Recurse -Filter "*.exe" | Where-Object { $_.Name -match '^mihomo.*\.exe$' } | Select-Object -First 1
if (-not $Exe) { throw "mihomo.exe not found in archive." }
Copy-Item -Force $Exe.FullName (Join-Path $CoreDir "mihomo.exe")
Write-Host "Installed successfully." -ForegroundColor Green
& (Join-Path $CoreDir "mihomo.exe") -v
Read-Host "Press Enter to close"

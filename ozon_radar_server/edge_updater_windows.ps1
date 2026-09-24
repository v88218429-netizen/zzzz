param(
  [string]$Base = "$env:ProgramData\OzonEdge",
  [string]$ManifestUrl = "https://raw.githubusercontent.com/v88218429-netizen/zzzz/mainggg/ozon_radar_server/edge_release_windows.json",
  [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$Stage = Join-Path $Base "_stage"
$Backup = Join-Path $Base "_rollback"
$VersionFile = Join-Path $Base "version.txt"
$LogDir = Join-Path $Base "logs"
$UpdateLog = Join-Path $LogDir "updater.log"
New-Item -ItemType Directory -Force -Path $Base,$Stage,$Backup,$LogDir | Out-Null

function Log($m) {
  $line = "$(Get-Date -Format s) $m"
  Add-Content -Path $UpdateLog -Value $line
}

try {
  $manifest = Invoke-RestMethod -UseBasicParsing -Uri $ManifestUrl -TimeoutSec 20
  if (-not $manifest.version -or -not $manifest.ref -or -not $manifest.files) { throw "Invalid release manifest" }

  $current = ""
  if (Test-Path $VersionFile) { $current = (Get-Content $VersionFile -Raw).Trim() }
  if ($current -eq [string]$manifest.version) { exit 0 }

  Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $Stage | Out-Null

  foreach ($f in $manifest.files) {
    $name = [string]$f
    $url = "https://raw.githubusercontent.com/v88218429-netizen/zzzz/$($manifest.ref)/ozon_radar_server/$name"
    $dst = Join-Path $Stage $name
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $dst -TimeoutSec 30
    if ((Get-Item $dst).Length -lt 20) { throw "Downloaded file is unexpectedly small: $name" }
  }

  $py = Get-Command python.exe -ErrorAction Stop
  & $py.Source -m py_compile (Join-Path $Stage "edge_agent_windows.py")
  if ($LASTEXITCODE -ne 0) { throw "Python validation failed" }

  foreach ($psName in @("run_edge_windows.ps1","edge_updater_windows.ps1")) {
    $p = Join-Path $Stage $psName
    if (Test-Path $p) { [void][scriptblock]::Create((Get-Content $p -Raw)) }
  }

  if ($ValidateOnly) {
    Log "Validated release $($manifest.version) ref $($manifest.ref)"
    Write-Host "VALIDATION_OK version=$($manifest.version) ref=$($manifest.ref)"
    exit 0
  }

  Remove-Item $Backup -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $Backup | Out-Null
  foreach ($f in $manifest.files) {
    $src = Join-Path $Base ([string]$f)
    if (Test-Path $src) { Copy-Item $src (Join-Path $Backup ([string]$f)) -Force }
  }

  foreach ($f in $manifest.files) {
    Copy-Item (Join-Path $Stage ([string]$f)) (Join-Path $Base ([string]$f)) -Force
  }

  Set-Content -Path $VersionFile -Value ([string]$manifest.version) -NoNewline

  if (Get-ScheduledTask -TaskName "OzonEdgeAgent" -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName "OzonEdgeAgent" -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName "OzonEdgeAgent"
  }

  $healthy = $false
  for ($i=0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
      $h = Invoke-RestMethod -Uri "http://127.0.0.1:8900/health" -TimeoutSec 2
      if ($h.ok) { $healthy = $true; break }
    } catch {}
  }

  if (-not $healthy) {
    Log "Release $($manifest.version) failed health check; rolling back"
    foreach ($f in $manifest.files) {
      $old = Join-Path $Backup ([string]$f)
      if (Test-Path $old) { Copy-Item $old (Join-Path $Base ([string]$f)) -Force }
    }
    Stop-ScheduledTask -TaskName "OzonEdgeAgent" -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName "OzonEdgeAgent" -ErrorAction SilentlyContinue
    throw "Health check failed; rollback applied"
  }

  Log "Updated successfully to $($manifest.version) ref $($manifest.ref)"
}
catch {
  Log ("UPDATE ERROR: " + $_.Exception.Message)
  throw
}

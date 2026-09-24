param([switch]$SkipTailscaleLogin)
$ErrorActionPreference = "Stop"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Run PowerShell as Administrator" }

$Base = "$env:ProgramData\OzonEdge"
$Logs = Join-Path $Base "logs"
New-Item -ItemType Directory -Force -Path $Base,$Logs | Out-Null

function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
}

function Ensure-Winget {
  if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) { throw "winget is required. Install App Installer, then rerun." }
}

function Ensure-Package([string]$Id) {
  Ensure-Winget
  winget install --id $Id --exact --silent --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0) { throw "winget install failed: $Id" }
  Refresh-Path
}

if (-not (Get-Command python.exe -ErrorAction SilentlyContinue)) { Ensure-Package "Python.Python.3.12" }
$Python = (Get-Command python.exe -ErrorAction Stop).Source
& $Python -m pip install --disable-pip-version-check --upgrade websocket-client
if ($LASTEXITCODE -ne 0) { throw "websocket-client install failed" }

$Tailscale = Get-Command tailscale.exe -ErrorAction SilentlyContinue
if (-not $Tailscale) {
  Ensure-Package "Tailscale.Tailscale"
  $Tailscale = Get-Command tailscale.exe -ErrorAction SilentlyContinue
}
if (-not $Tailscale) {
  $candidate = "$env:ProgramFiles\Tailscale\tailscale.exe"
  if (Test-Path $candidate) { $Tailscale = Get-Item $candidate }
}
if (-not $Tailscale) { throw "Tailscale CLI not found after installation" }

$repo = "https://raw.githubusercontent.com/v88218429-netizen/zzzz/mainggg/ozon_radar_server"
Invoke-WebRequest -UseBasicParsing "$repo/edge_updater_windows.ps1" -OutFile (Join-Path $Base "edge_updater_windows.ps1") -TimeoutSec 30

powercfg /change standby-timeout-ac 0 | Out-Null
powercfg /change hibernate-timeout-ac 0 | Out-Null
powercfg /change monitor-timeout-ac 5 | Out-Null

Get-NetFirewallRule -DisplayName "Ozon Edge Agent - Tailscale" -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "Ozon Edge Agent - Tailscale" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8900 -RemoteAddress "100.64.0.0/10" | Out-Null

if (-not $SkipTailscaleLogin) {
  Write-Host ""
  Write-Host "Tailscale one-time authorization may open in the browser."
  & $Tailscale.Source up --unattended=true
  if ($LASTEXITCODE -ne 0) { throw "Tailscale login/configuration failed" }
}

$AgentArg = "-NoProfile -ExecutionPolicy Bypass -File `"$Base\run_edge_windows.ps1`""
$AgentAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $AgentArg
$AgentTrigger = New-ScheduledTaskTrigger -AtLogOn
$AgentSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Days 3650) -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "OzonEdgeAgent" -Action $AgentAction -Trigger $AgentTrigger -Settings $AgentSettings -RunLevel Highest -Force | Out-Null

$UpdaterArg = "-NoProfile -ExecutionPolicy Bypass -File `"$Base\edge_updater_windows.ps1`""
$UpdaterAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $UpdaterArg
$UpdaterTrigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 2)
$UpdaterSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -StartWhenAvailable
Register-ScheduledTask -TaskName "OzonEdgeUpdater" -Action $UpdaterAction -Trigger $UpdaterTrigger -Settings $UpdaterSettings -RunLevel Highest -Force | Out-Null

& (Join-Path $Base "edge_updater_windows.ps1")
if ($LASTEXITCODE -ne 0) { throw "Initial edge update failed" }

Start-ScheduledTask -TaskName "OzonEdgeAgent"
Start-Sleep -Seconds 10

try {
  $health = Invoke-RestMethod -Uri "http://127.0.0.1:8900/health" -TimeoutSec 5
  Write-Host ""
  Write-Host "OZON EDGE HEALTH: $($health.ok) VERSION: $($health.version)"
} catch {
  Write-Warning "Agent did not become healthy. Check $Logs\agent.log and $Logs\updater.log"
}

Write-Host ""
Write-Host "INSTALL COMPLETE"
Write-Host "Base: $Base"
Write-Host "Auto-update: every 2 minutes"
Write-Host "Tailscale: unattended mode requested"
Write-Host "Agent: starts on Windows logon"
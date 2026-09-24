param([string]$Base = "$env:ProgramData\OzonEdge")
$ErrorActionPreference = "Stop"

$Profile = Join-Path $Base "BrowserProfile"
$Logs = Join-Path $Base "logs"
New-Item -ItemType Directory -Force -Path $Profile,$Logs | Out-Null

$Edge = "$env:ProgramFiles(x86)\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $Edge)) { $Edge = "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe" }
$Chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $Chrome)) { $Chrome = "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe" }

if (Test-Path $Edge) { $Browser = $Edge }
elseif (Test-Path $Chrome) { $Browser = $Chrome }
else { throw "Edge/Chrome not found" }

Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like "*--remote-debugging-port=9222*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$Args = @(
  "--remote-debugging-address=127.0.0.1",
  "--remote-debugging-port=9222",
  "--user-data-dir=$Profile",
  "--no-first-run",
  "--no-default-browser-check",
  "--start-minimized",
  "https://www.ozon.ru/"
)
Start-Process -FilePath $Browser -ArgumentList $Args

$Ready = $false
for ($i=0; $i -lt 60; $i++) {
  try {
    Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:9222/json/version" -TimeoutSec 2 | Out-Null
    $Ready = $true
    break
  } catch {
    Start-Sleep -Seconds 1
  }
}
if (-not $Ready) { throw "Browser CDP did not start" }

$Python = (Get-Command python.exe -ErrorAction Stop).Source
$Agent = Join-Path $Base "edge_agent_windows.py"
& $Python $Agent *>> (Join-Path $Logs "agent.log")

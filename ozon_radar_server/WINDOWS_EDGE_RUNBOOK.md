# Ozon Windows Edge — runbook

## Production model
- Windows 10/11 edge node on residential internet.
- Edge/Chrome with a dedicated persistent browser profile.
- Local agent: `edge_agent_windows.py` on TCP 8900.
- Railway reaches the node over Tailscale.
- Windows Tailscale runs in unattended mode.
- Agent starts at Windows logon.
- Updater checks for a release every 2 minutes.

## Release files
- `edge_agent_windows.py`
- `run_edge_windows.ps1`
- `edge_updater_windows.ps1`
- `edge_release_windows.json`

## Safe release procedure
1. Edit code files on branch `mainggg`.
2. Wait for Windows validation workflow to pass.
3. Use the latest code commit SHA as `ref` in `edge_release_windows.json`.
4. Increment `version`.
5. Commit the manifest last.
6. Edge nodes download that exact commit, validate locally, backup current files, apply, restart, run /health.
7. If health fails, updater restores `_rollback` automatically.

Never point a release manifest at a moving branch.

## Local paths
- Base: `C:\ProgramData\OzonEdge`
- Browser profile: `C:\ProgramData\OzonEdge\BrowserProfile`
- Logs: `C:\ProgramData\OzonEdge\logs`
- Current release: `C:\ProgramData\OzonEdge\version.txt`
- Rollback: `C:\ProgramData\OzonEdge\_rollback`

## Scheduled tasks
- `OzonEdgeAgent` — starts at user logon.
- `OzonEdgeUpdater` — checks release every 2 minutes.

## Local diagnostics
- `http://127.0.0.1:8900/health`
- `http://127.0.0.1:8900/version`
- `http://127.0.0.1:8900/diagnostics`

## Routine maintenance
Routine parser/code changes should be performed through GitHub release updates, not by manually editing the Windows machine.

For OS-level repair, Remote Desktop Commander can be paired as an optional emergency channel, but it is not a production dependency.

## Current validated release
- Version: 0.2.1
- Ref: 9e0f5f77db8d4d19180b4aa19e0f10cc7824cab8
- Windows validation workflow: SUCCESS

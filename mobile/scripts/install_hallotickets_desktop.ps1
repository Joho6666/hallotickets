$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..\..")).Path
$launcher = Join-Path $scriptDir "start_status_dashboard.ps1"
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "HalloTickets.lnk"
$iconPath = Join-Path $repoRoot "mobile\dashboard\hallotickets-ticket.ico"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $PSHOME "powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`" -Port 8774"
$shortcut.WorkingDirectory = $repoRoot
if (Test-Path $iconPath) { $shortcut.IconLocation = $iconPath }
$shortcut.Description = "Open HalloTickets standalone app window"
$shortcut.Save()

Write-Output "HalloTickets desktop shortcut created."

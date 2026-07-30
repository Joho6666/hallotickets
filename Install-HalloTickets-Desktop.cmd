@echo off
setlocal

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0mobile\scripts\install_hallotickets_desktop.ps1"
if errorlevel 1 (
  echo.
  echo Desktop shortcut installation failed. Check PowerShell permissions.
  pause
  exit /b 1
)

echo.
echo HalloTickets desktop shortcut is ready.
pause

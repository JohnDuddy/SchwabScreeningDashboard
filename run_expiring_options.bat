@echo off
REM Launch the Schwab app directly on the Expiring Options page.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_app.ps1" -LandingPath "/expiring-options" -LogPath "shortcut_launch.log"
pause

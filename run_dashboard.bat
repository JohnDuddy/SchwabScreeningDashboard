@echo off
REM Schwab Covered Call Dashboard launcher.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_app.ps1" -LandingPath "/" -LogPath "shortcut_launch.log"
pause

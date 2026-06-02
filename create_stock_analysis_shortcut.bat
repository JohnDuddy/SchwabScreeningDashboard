@echo off
REM Creates (or replaces) the "Stock Detailed Analysis" desktop shortcut.
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_make_stock_analysis_shortcut.ps1" "%~dp0"

echo.
echo Done. Double-click "Stock Detailed Analysis" on your desktop to launch.

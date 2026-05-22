@echo off
REM run.bat – Schwab Covered Call Dashboard (Windows)
REM Right-click → Run as administrator, OR use the desktop shortcut.

cd /d "%~dp0"

IF NOT EXIST venv (
    echo Creating virtual environment...
    python -m venv venv
)

call venv\Scripts\activate.bat

pip install -q -r requirements.txt

IF NOT EXIST .env (
    echo.
    echo ERROR: .env file not found.
    echo Run:  copy .env.example .env  and fill in your Schwab credentials.
    pause
    exit /b 1
)

echo.
echo Starting Schwab Covered Call Dashboard (HTTPS)...
echo Browser will open automatically.
echo Press Ctrl+C to stop.
echo.

REM Stop any stale dashboard instance already bound to HTTPS port 443.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -State Listen -LocalPort 443 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

REM Open the browser after a short delay (gives server time to start)
start "" cmd /c "timeout /t 3 /nobreak > nul & start https://127.0.0.1"

python app.py
pause

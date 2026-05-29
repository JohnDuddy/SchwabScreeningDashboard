@echo off
REM Schwab Covered Call Dashboard — full launcher.
REM All background scans (CSP, momentum, options, 0DTE, expiring) start automatically.
REM Must run as Administrator to bind HTTPS on port 443.
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
    echo Copy .env.example to .env and fill in your Schwab credentials.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Schwab Covered Call Dashboard
echo   Starting all scans automatically...
echo   Browser will open in a few seconds.
echo   Press Ctrl+C to stop the server.
echo ============================================================
echo.

REM Kill any process already holding port 443 (e.g. a previous run)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Get-NetTCPConnection -State Listen -LocalPort 443 -ErrorAction SilentlyContinue ^
     | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

REM Open the dashboard in the default browser after Flask has had time to bind
start "" cmd /c "timeout /t 5 /nobreak > nul & start https://127.0.0.1"

python app.py
pause

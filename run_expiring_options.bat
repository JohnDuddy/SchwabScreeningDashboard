@echo off
REM Launch the existing Schwab app directly on the Expiring Options page.
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
    echo Run: copy .env.example .env and fill in your Schwab credentials.
    pause
    exit /b 1
)

echo.
echo Starting Expiring Options scanner page...
echo Press Ctrl+C to stop.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -State Listen -LocalPort 443 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
start "" cmd /c "timeout /t 3 /nobreak > nul & start https://127.0.0.1/expiring-options"
python app.py
pause

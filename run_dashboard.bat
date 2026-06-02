@echo off
REM Schwab Covered Call Dashboard — full launcher.
REM All background scans (CSP, momentum, options, 0DTE, expiring) start automatically.
cd /d "%~dp0"

echo [%date% %time%] Schwab Dashboard launcher started. > shortcut_launch.log
echo Working directory: %CD% >> shortcut_launch.log
echo User: %USERNAME% >> shortcut_launch.log

IF NOT EXIST venv (
    echo Creating virtual environment...
    echo Creating virtual environment... >> shortcut_launch.log
    python -m venv venv
)

call venv\Scripts\activate.bat
echo Virtual environment activated. >> shortcut_launch.log
pip install -q -r requirements.txt >> shortcut_launch.log 2>&1
IF ERRORLEVEL 1 (
    echo.
    echo ERROR: dependency install failed. See shortcut_launch.log.
    echo Dependency install failed. >> shortcut_launch.log
    pause
    exit /b 1
)

IF NOT EXIST .env (
    echo.
    echo ERROR: .env file not found.
    echo Copy .env.example to .env and fill in your Schwab credentials.
    echo Missing .env file. >> shortcut_launch.log
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Schwab Covered Call Dashboard
echo   Starting all scans automatically...
echo   Browser will open when the server is ready.
echo   Press Ctrl+C to stop the server.
echo ============================================================
echo.

REM Kill any process already holding port 443 (e.g. a previous run)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$listeners = Get-NetTCPConnection -State Listen -LocalPort 443 -ErrorAction SilentlyContinue; foreach ($listener in $listeners) { Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue }"
echo Cleared stale port 443 listeners. >> shortcut_launch.log

REM Open the dashboard only after Flask is actually listening on HTTPS.
REM Startup can take longer than a fixed delay when caches or scans initialize.
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "$url='https://127.0.0.1/'; for ($i=0; $i -lt 90; $i++) { if (Test-NetConnection -ComputerName 127.0.0.1 -Port 443 -InformationLevel Quiet) { Start-Process $url; exit }; Start-Sleep -Seconds 1 }"

echo Starting app.py. >> shortcut_launch.log
python app.py >> shortcut_launch.log 2>&1
echo app.py exited with code %ERRORLEVEL%. >> shortcut_launch.log
pause

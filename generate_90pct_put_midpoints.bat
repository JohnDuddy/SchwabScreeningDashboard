@echo off
REM Generate CSV and Excel files for 90% current-price put midpoint premiums.
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

python export_90pct_put_midpoints.py
pause

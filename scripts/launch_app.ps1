param(
    [string]$LandingPath = "/",
    [int]$Port = 443,
    [string]$LogPath = "shortcut_launch.log",
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

function Write-LaunchLog {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LogPath -Value "[$stamp] $Message"
}

Write-LaunchLog "Schwab launcher started. LandingPath=$LandingPath Port=$Port"
Write-LaunchLog "Working directory: $ProjectRoot"
Write-LaunchLog "User: $env:USERNAME"

if (-not (Test-Path "venv")) {
    Write-Host "Creating virtual environment..."
    Write-LaunchLog "Creating virtual environment."
    python -m venv venv
}

$Activate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
if (-not (Test-Path $Activate)) {
    throw "Virtual environment activation script not found: $Activate"
}
. $Activate
Write-LaunchLog "Virtual environment activated."

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$pipOutput = & python -m pip install -q -r requirements.txt 2>&1
$pipExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorActionPreference
if ($pipExitCode -ne 0) {
    if ($pipOutput) {
        Add-Content -Path $LogPath -Value $pipOutput
    }
    Write-LaunchLog "Dependency install failed."
    throw "Dependency install failed. See $LogPath."
}

if (-not (Test-Path ".env")) {
    Write-Host ""
    Write-Host "ERROR: .env file not found."
    Write-Host "Copy .env.example to .env and fill in your Schwab credentials."
    Write-LaunchLog "Missing .env file."
    exit 1
}

if ($ValidateOnly) {
    Write-Host "Launcher validation passed."
    Write-LaunchLog "Launcher validation passed."
    exit 0
}

Write-Host ""
Write-Host "============================================================"
Write-Host "  Schwab Covered Call Dashboard"
Write-Host "  Landing page: https://127.0.0.1$LandingPath"
Write-Host "  Press Ctrl+C to stop the server."
Write-Host "============================================================"
Write-Host ""

$listeners = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
foreach ($listener in $listeners) {
    Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
}
Write-LaunchLog "Cleared stale port $Port listeners."

$url = "https://127.0.0.1$LandingPath"
Start-Process powershell -WindowStyle Hidden -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-Command",
    "`$url='$url'; `$port=$Port; for (`$i=0; `$i -lt 90; `$i++) { if (Test-NetConnection -ComputerName 127.0.0.1 -Port `$port -InformationLevel Quiet) { Start-Process `$url; exit }; Start-Sleep -Seconds 1 }"
)

Write-LaunchLog "Starting app.py."
python app.py *>> $LogPath
Write-LaunchLog "app.py exited with code $LASTEXITCODE."
exit $LASTEXITCODE

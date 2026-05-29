@echo off
REM Creates (or replaces) the "Schwab Dashboard" desktop shortcut.
REM The shortcut is flagged "Run as Administrator" so the app can bind to port 443.
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
"$ws  = New-Object -ComObject WScript.Shell; ^
$lnk  = [IO.Path]::Combine($ws.SpecialFolders('Desktop'), 'Schwab Dashboard.lnk'); ^
$sc   = $ws.CreateShortcut($lnk); ^
$sc.TargetPath      = '%~dp0run_dashboard.bat'; ^
$sc.WorkingDirectory= '%~dp0'; ^
$sc.IconLocation    = '%SystemRoot%\System32\imageres.dll,2'; ^
$sc.Description     = 'Schwab Covered Call Dashboard - all scans'; ^
$sc.WindowStyle     = 1; ^
$sc.Save(); ^
$b = [IO.File]::ReadAllBytes($lnk); ^
$b[0x15] = $b[0x15] -bor 0x20; ^
[IO.File]::WriteAllBytes($lnk, $b); ^
Write-Host 'Desktop shortcut created: Schwab Dashboard';"

echo.
echo Done. Double-click "Schwab Dashboard" on your desktop to launch.

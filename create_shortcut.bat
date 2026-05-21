@echo off
REM create_shortcut.bat — Creates a desktop shortcut for the Schwab Dashboard
REM Double-click this file once. A "Schwab Dashboard" icon will appear on your Desktop.
REM The shortcut is configured to always Run as Administrator.

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
"$ws = New-Object -ComObject WScript.Shell; ^
$sc = $ws.CreateShortcut([IO.Path]::Combine($ws.SpecialFolders('Desktop'), 'Schwab Dashboard.lnk')); ^
$sc.TargetPath = '%~dp0run.bat'; ^
$sc.WorkingDirectory = '%~dp0'; ^
$sc.IconLocation = 'shell32.dll,12'; ^
$sc.Description = 'Schwab Covered Call Dashboard - runs on https://127.0.0.1'; ^
$sc.Save(); ^
Write-Host 'Shortcut created on Desktop: Schwab Dashboard'; ^
Write-Host ''; ^
Write-Host 'IMPORTANT: Right-click the shortcut → Properties → Advanced'; ^
Write-Host '           Check [Run as administrator] → OK → OK'; ^
Write-Host ''; ^
Write-Host 'After that one-time step, just double-click the icon to launch.';"

echo.
echo Done! Check your Desktop for "Schwab Dashboard" shortcut.
echo.
echo NEXT STEP: Right-click the new shortcut → Properties → Advanced
echo            Check "Run as administrator" → OK → OK
echo.
pause

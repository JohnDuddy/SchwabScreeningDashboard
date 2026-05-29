@echo off
REM Creates a desktop shortcut for the Expiring Options page.
REM The shortcut is marked to run as administrator because the app binds to HTTPS port 443.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
"$ws = New-Object -ComObject WScript.Shell; ^
$path = [IO.Path]::Combine($ws.SpecialFolders('Desktop'), 'Expiring Options.lnk'); ^
$sc = $ws.CreateShortcut($path); ^
$sc.TargetPath = '%~dp0run_expiring_options.bat'; ^
$sc.WorkingDirectory = '%~dp0'; ^
$sc.IconLocation = 'shell32.dll,13'; ^
$sc.Description = 'Schwab Expiration-Day Put Scanner'; ^
$sc.Save(); ^
$bytes = [IO.File]::ReadAllBytes($path); ^
$bytes[0x15] = $bytes[0x15] -bor 0x20; ^
[IO.File]::WriteAllBytes($path, $bytes); ^
Write-Host 'Shortcut created on Desktop: Expiring Options';"
echo Done.
pause

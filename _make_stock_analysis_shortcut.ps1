param([string]$ProjectDir)
$ws  = New-Object -ComObject WScript.Shell
$lnk = [IO.Path]::Combine($ws.SpecialFolders('Desktop'), 'Stock Detailed Analysis.lnk')
$sc  = $ws.CreateShortcut($lnk)
$sc.TargetPath       = "$ProjectDir\run_dashboard.bat"
$sc.Arguments        = ''
$sc.WorkingDirectory = $ProjectDir
$sc.IconLocation     = "$env:SystemRoot\System32\imageres.dll,155"
$sc.Description      = 'Stock Detailed Analysis - Multi-Model Equity Ranking Dashboard'
$sc.WindowStyle      = 1
$sc.Save()
$b = [IO.File]::ReadAllBytes($lnk)
$b[0x15] = $b[0x15] -band (-bnot 0x20)
[IO.File]::WriteAllBytes($lnk, $b)
Write-Host "Desktop shortcut created: Stock Detailed Analysis"

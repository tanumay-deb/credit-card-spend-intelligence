<#
.SYNOPSIS
Sets up the daily check and the dashboard shortcut, or removes them.

.DESCRIPTION
Creates the "CreditCard daily check" scheduled task, which runs daily_run.py
every day, and a "Credit Card Dashboard" desktop shortcut that opens the
dashboard and refreshes it.

The task runs as you, only while you are logged in (Windows notifications need
your session), with no console window. If the PC is off or asleep at the set
time, it runs as soon as it can, and it runs on battery too.

.PARAMETER At
Time of the daily check, 24-hour. Default 12:00.

.PARAMETER Uninstall
Remove the task and the shortcut.
#>
param(
    [string]$At = '12:00',
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$taskName = 'CreditCard daily check'
$shortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Credit Card Dashboard.lnk'

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item $shortcut -ErrorAction SilentlyContinue
    Write-Output 'Removed the daily check and the desktop shortcut.'
    exit 0
}

$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) {
    throw "Missing $pythonw - create the virtual environment first (see README, Quick start)."
}

# Block commits that would put passwords, statements or outputs into git.
git -C $root config core.hooksPath .githooks

# Point the dashboard's ProjectFolder parameter at wherever this project lives.
Push-Location $root
try { & (Join-Path $root '.venv\Scripts\python.exe') -m creditcard.powerbi }
finally { Pop-Location }

$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument ('"' + (Join-Path $root 'daily_run.py') + '"') -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$principal = New-ScheduledTaskPrincipal -LogonType Interactive -RunLevel Limited `
    -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description 'Downloads new credit card statements, imports them, refreshes the dashboard and sends alerts. See README.' |
    Out-Null

$link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
$link.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$link.Arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' +
    (Join-Path $PSScriptRoot 'open_dashboard.ps1') + '"'
$link.WorkingDirectory = $root
$link.Description = 'Opens the credit card dashboard and refreshes it'
$pbi = Join-Path $env:ProgramFiles 'Microsoft Power BI Desktop\bin\PBIDesktop.exe'
if (Test-Path $pbi) { $link.IconLocation = "$pbi,0" }
$link.Save()

Write-Output "The daily check runs every day at $At, and 'Credit Card Dashboard' is on your desktop."

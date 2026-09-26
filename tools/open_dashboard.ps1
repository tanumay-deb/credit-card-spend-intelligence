<#
.SYNOPSIS
Opens the credit card dashboard, then refreshes it once it has loaded.

.DESCRIPTION
Target of the "Credit Card Dashboard" desktop shortcut that tools/install.ps1
creates. Power BI Desktop opens a report with the data it held when it was last
saved; this presses Refresh once the report is ready, so the dashboard shows
whatever the daily check has imported since.
#>
param(
    # Read the data from this folder instead of the project's own (the demo uses demo/).
    [string]$Folder = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$refresh = Join-Path $PSScriptRoot 'refresh_dashboard.ps1'

& $refresh -FindOnly | Out-Null
if ($LASTEXITCODE -eq 3) {
    # Point the dashboard's ProjectFolder parameter at wherever this project lives.
    Push-Location $root
    $folderArgs = if ($Folder) { @('--folder', $Folder) } else { @() }
    try { & (Join-Path $root '.venv\Scripts\python.exe') -m creditcard.powerbi @folderArgs | Out-Null }
    finally { Pop-Location }
    Start-Process (Join-Path $root 'dashboard.pbip')
}
& $refresh -WaitSeconds 300 -Activate
exit $LASTEXITCODE

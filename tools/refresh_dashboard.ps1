<#
.SYNOPSIS
Refreshes the open credit card dashboard by pressing Power BI Desktop's own Refresh button.

.DESCRIPTION
Microsoft does not support sending refresh (processing) commands to a model open in
Power BI Desktop. So this presses the Home ribbon's Refresh button through Windows UI
Automation - what a mouse click does - and confirms the refresh by reading the model's
refresh time, a read-only query that external tools may run.

Runs in Windows PowerShell 5.1. Exit codes: 0 refreshed (with -FindOnly: open),
3 the dashboard is not open, 1 failed.

.PARAMETER WaitSeconds
How long to wait for the dashboard to open and load before giving up. 0 checks once.

.PARAMETER TimeoutSeconds
How long the refresh itself may take.

.PARAMETER FindOnly
Only report whether the dashboard is open.

.PARAMETER Activate
Bring the Power BI window to the front first.
#>
param(
    [int]$WaitSeconds = 0,
    [int]$TimeoutSeconds = 300,
    [switch]$FindOnly,
    [switch]$Activate
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$AE = [System.Windows.Automation.AutomationElement]
$Scope = [System.Windows.Automation.TreeScope]
$ControlType = [System.Windows.Automation.ControlType]

function New-Condition([System.Windows.Automation.AutomationProperty]$Property, $Value) {
    New-Object System.Windows.Automation.PropertyCondition($Property, $Value)
}

function Get-RefreshedTime([string]$Port) {
    # When fact_transactions last loaded, or $null if this model has no such table.
    $conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$Port")
    try {
        $conn.Open()
        $cmd = $conn.CreateCommand()
        $cmd.CommandText = 'SELECT [Name], [RefreshedTime] FROM $SYSTEM.TMSCHEMA_PARTITIONS'
        $reader = $cmd.ExecuteReader()
        try {
            while ($reader.Read()) {
                if ($reader.GetValue(0) -eq 'fact_transactions') { return [datetime]$reader.GetValue(1) }
            }
        } finally { $reader.Close() }
        return $null
    } catch {
        return $null
    } finally {
        $conn.Close()
    }
}

function Get-Dashboard {
    # Each PBIDesktop.exe runs its own msmdsrv.exe engine, whose -s argument is the
    # workspace folder holding msmdsrv.port.txt. The dashboard is the model with a
    # fact_transactions table, so another open report is never touched.
    foreach ($pbi in @(Get-CimInstance Win32_Process -Filter "Name = 'PBIDesktop.exe'")) {
        $engine = Get-CimInstance Win32_Process -Filter "Name = 'msmdsrv.exe' AND ParentProcessId = $($pbi.ProcessId)" |
            Select-Object -First 1
        if (-not $engine -or $engine.CommandLine -notmatch '-s\s+"([^"]+)"') { continue }
        $portFile = Join-Path $Matches[1] 'msmdsrv.port.txt'
        if (-not (Test-Path $portFile)) { continue }
        $port = (Get-Content $portFile -Encoding Unicode -Raw).Trim()
        if (-not ('Microsoft.AnalysisServices.AdomdClient.AdomdConnection' -as [type])) {
            Add-Type -Path (Join-Path (Split-Path $pbi.ExecutablePath) 'Microsoft.PowerBI.AdomdClient.dll')
        }
        if ($null -ne (Get-RefreshedTime $port)) {
            return [pscustomobject]@{ ProcessId = [int]$pbi.ProcessId; Port = $port }
        }
    }
    return $null
}

function Get-MainWindow([int]$ProcessId) {
    $handle = (Get-Process -Id $ProcessId).MainWindowHandle
    if ($handle -eq 0) { return $null }
    return $AE::FromHandle($handle)
}

function Find-RefreshButton([int]$ProcessId) {
    # The Home ribbon's Refresh is a split button; its primary half does the refresh.
    $window = Get-MainWindow $ProcessId
    if (-not $window) { return $null }
    foreach ($attempt in 1..2) {
        $button = $window.FindAll($Scope::Descendants, (New-Condition $AE::NameProperty 'Refresh')) |
            Where-Object { $_.Current.ClassName -like 'splitPrimaryButton*' } | Select-Object -First 1
        if ($button) { return $button }
        # Another ribbon tab is showing: select Home and look again.
        $homeTab = $window.FindFirst($Scope::Descendants, (New-Object System.Windows.Automation.AndCondition(
            (New-Condition $AE::ControlTypeProperty $ControlType::TabItem),
            (New-Condition $AE::NameProperty 'Home'))))
        if (-not $homeTab) { return $null }
        $homeTab.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
        Start-Sleep -Seconds 1
    }
    return $null
}

function Test-RefreshDialog([int]$ProcessId) {
    # Power BI shows a "Refresh" dialog while loading; it stays open on an error.
    $window = Get-MainWindow $ProcessId
    if (-not $window) { return $false }
    $dialog = $window.FindFirst($Scope::Descendants, (New-Object System.Windows.Automation.AndCondition(
        (New-Condition $AE::NameProperty 'Refresh'),
        (New-Object System.Windows.Automation.OrCondition(
            (New-Condition $AE::ControlTypeProperty $ControlType::Window),
            (New-Condition $AE::LocalizedControlTypeProperty 'dialog'))))))
    return [bool]$dialog
}

try {
    $dashboard = Get-Dashboard
    if ($FindOnly) {
        if ($dashboard) { Write-Output 'The dashboard is open.'; exit 0 }
        Write-Output 'The dashboard is not open in Power BI Desktop.'; exit 3
    }

    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    $button = $null
    while ($true) {
        if ($dashboard) { $button = Find-RefreshButton $dashboard.ProcessId }
        if ($button -or (Get-Date) -ge $deadline) { break }
        Start-Sleep -Seconds 3
        $dashboard = Get-Dashboard
    }
    if (-not $dashboard) { Write-Output 'The dashboard is not open in Power BI Desktop.'; exit 3 }
    if (-not $button) { Write-Output 'Found the dashboard but not its Refresh button.'; exit 1 }

    if ($Activate) { (New-Object -ComObject WScript.Shell).AppActivate($dashboard.ProcessId) | Out-Null }
    $before = Get-RefreshedTime $dashboard.Port
    $button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        $after = Get-RefreshedTime $dashboard.Port
        if ($after -and $after -gt $before -and -not (Test-RefreshDialog $dashboard.ProcessId)) {
            Write-Output "Refreshed: fact_transactions loaded at $($after.ToString('s'))."
            exit 0
        }
    }
    Write-Output 'Pressed Refresh, but the refresh did not finish in time or showed an error.'
    exit 1
} catch {
    Write-Output "Refresh failed: $($_.Exception.GetType().Name): $($_.Exception.Message)"
    exit 1
}

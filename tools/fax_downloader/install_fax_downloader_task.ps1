param(
    [string]$TaskName = "Automated Fax Downloader",
    [int]$RepeatMinutes = 30,
    [switch]$CurrentUserOnly,
    [switch]$NoHighestPrivileges
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$vbsPath = Join-Path $scriptRoot "run_silently.vbs"

if (-not (Test-Path -LiteralPath $vbsPath)) {
    throw "Missing launcher script: $vbsPath"
}

if ($RepeatMinutes -lt 1) {
    throw "RepeatMinutes must be at least 1"
}

$requestedRunLevel = if ($NoHighestPrivileges) { "LIMITED" } else { "HIGHEST" }
$effectiveRunLevel = $requestedRunLevel
$taskRun = "wscript.exe `"$vbsPath`""
$mode = ""

function New-FaxDownloaderTask {
    param(
        [string]$RunLevel,
        [string]$RunAsUser = ""
    )

    $args = @(
        "/Create",
        "/TN", $TaskName,
        "/SC", "MINUTE",
        "/MO", $RepeatMinutes.ToString(),
        "/TR", $taskRun,
        "/F",
        "/RL", $RunLevel
    )

    if ($RunAsUser) {
        $args += @("/RU", $RunAsUser)
    }

    $null = & schtasks.exe @args
    return $LASTEXITCODE -eq 0
}

$created = $false

if (-not $CurrentUserOnly) {
    # Try strongest background mode first.
    $created = New-FaxDownloaderTask -RunLevel $effectiveRunLevel -RunAsUser "SYSTEM"
    if ($created) {
        $mode = "SYSTEM"
    }
    else {
        Write-Warning "SYSTEM task creation failed; falling back to current user."
    }
}

if (-not $created) {
    $created = New-FaxDownloaderTask -RunLevel $effectiveRunLevel
    if ($created) {
        $mode = "CurrentUser"
    }
}

if (-not $created -and $effectiveRunLevel -eq "HIGHEST") {
    Write-Warning "Highest privileges denied; retrying with LIMITED run level."
    $effectiveRunLevel = "LIMITED"
    if (-not $CurrentUserOnly) {
        $created = New-FaxDownloaderTask -RunLevel $effectiveRunLevel -RunAsUser "SYSTEM"
        if ($created) {
            $mode = "SYSTEM"
        }
    }
    if (-not $created) {
        $created = New-FaxDownloaderTask -RunLevel $effectiveRunLevel
        if ($created) {
            $mode = "CurrentUser"
        }
    }
}

if (-not $created) {
    throw "Failed to create scheduled task '$TaskName'. Try running PowerShell as Administrator."
}

Write-Host "Task '$TaskName' created/updated."
Write-Host "Runs every $RepeatMinutes minutes via: $vbsPath"
Write-Host "Mode: $mode; RunLevel: $effectiveRunLevel"
Write-Host "Use Task Scheduler -> Run to test once immediately."

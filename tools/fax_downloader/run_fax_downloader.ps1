$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $scriptRoot)
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
$scriptPath = Join-Path $scriptRoot "fax_downloader.py"
$envPath = Join-Path $scriptRoot ".env"
$logPath = Join-Path $scriptRoot "fax_downloader_task.log"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Python not found at $pythonExe. Create .venv first or update run_fax_downloader.ps1."
}

if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "Script not found: $scriptPath"
}

if (Test-Path -LiteralPath $envPath) {
    foreach ($line in Get-Content -LiteralPath $envPath) {
        $trimmed = $line.Trim()
        if (-not $trimmed) { continue }
        if ($trimmed.StartsWith("#")) { continue }
        $parts = $trimmed -split "=", 2
        if ($parts.Count -ne 2) { continue }
        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"')
        if ($name) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -LiteralPath $logPath -Value "[$timestamp] Starting fax downloader"

$prevNativePref = $null
$hasNativePref = $false
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue) {
    $hasNativePref = $true
    $prevNativePref = $global:PSNativeCommandUseErrorActionPreference
    $global:PSNativeCommandUseErrorActionPreference = $false
}

$exitCode = 1
try {
    & $pythonExe $scriptPath *>> $logPath
    $exitCode = $LASTEXITCODE
}
catch {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $logPath -Value "[$timestamp] Wrapper error: $($_.Exception.Message)"
    $exitCode = 1
}
finally {
    if ($hasNativePref) {
        $global:PSNativeCommandUseErrorActionPreference = $prevNativePref
    }
}

if ($exitCode -ne 0) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $logPath -Value "[$timestamp] Hint: If using Gmail, consider OAuth mode (setup_gmail_oauth.py)."
}

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -LiteralPath $logPath -Value "[$timestamp] Exit code: $exitCode"

exit $exitCode

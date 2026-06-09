$ErrorActionPreference = 'SilentlyContinue'

$logDir = Join-Path $env:LOCALAPPDATA 'DMELogic\Logs'
if (-not (Test-Path $logDir)) {
    New-Item -Path $logDir -ItemType Directory -Force | Out-Null
}

$logPath = Join-Path $logDir 'nova_wake_listener.log'
$statePath = Join-Path $logDir 'nova_wake_listener_state.json'

$created = $false
$mutex = New-Object System.Threading.Mutex($false, 'Local\DMELogicNovaWakeListener', [ref]$created)
if (-not $created) {
    Add-Content -Path $logPath -Value "$(Get-Date -Format o) Existing wake listener instance detected; exiting."
    exit 0
}

$win32Sig = @'
[DllImport("user32.dll")]
public static extern bool SetForegroundWindow(IntPtr hWnd);
[DllImport("user32.dll")]
public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
[DllImport("user32.dll")]
public static extern bool IsIconic(IntPtr hWnd);
'@

try {
    Add-Type -MemberDefinition $win32Sig -Name NovaNative -Namespace Win32 -ErrorAction Stop
} catch {}

function Find-NovaWindow {
    return Get-Process -Name 'chrome','msedge' -ErrorAction SilentlyContinue |
        Where-Object {
            $_.MainWindowHandle -ne 0 -and
            ($_.MainWindowTitle -match 'Nova|NOVA|127\.0\.0\.1:8401')
        } |
        Select-Object -First 1
}

function Focus-NovaWindow {
    $proc = Find-NovaWindow
    if ($null -eq $proc) { return $false }

    try {
        $hwnd = $proc.MainWindowHandle
        if ([Win32.NovaNative]::IsIconic($hwnd)) {
            [Win32.NovaNative]::ShowWindow($hwnd, 9) | Out-Null
        }
        [Win32.NovaNative]::SetForegroundWindow($hwnd) | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Save-LastBrowserPid([int]$BrowserPid) {
    try {
        @{ browser_pid = $BrowserPid; updated_at = (Get-Date -Format o) } |
            ConvertTo-Json -Compress |
            Set-Content -Path $statePath -Encoding UTF8
    } catch {}
}

function Focus-LastBrowserFromState {
    try {
        if (-not (Test-Path $statePath)) { return $false }
        $stateRaw = Get-Content $statePath -Raw
        if (-not $stateRaw) { return $false }

        $state = $stateRaw | ConvertFrom-Json
        $savedPid = [int]($state.browser_pid)
        if ($savedPid -le 0) { return $false }

        $existing = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
        if ($null -eq $existing) { return $false }
        if ($existing.MainWindowHandle -eq 0) { return $false }

        $hwnd = $existing.MainWindowHandle
        if ([Win32.NovaNative]::IsIconic($hwnd)) {
            [Win32.NovaNative]::ShowWindow($hwnd, 9) | Out-Null
        }
        [Win32.NovaNative]::SetForegroundWindow($hwnd) | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Open-NovaBrowser {
    if (Focus-LastBrowserFromState) {
        return
    }

    $chromePaths = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LocalAppData\Google\Chrome\Application\chrome.exe"
    )
    $chromeExe = $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

    if ($chromeExe) {
        $proc = Start-Process $chromeExe '--app=http://127.0.0.1:8401/' -WindowStyle Normal -PassThru
        if ($proc) {
            Save-LastBrowserPid -BrowserPid $proc.Id
        }
        return
    }

    $edgePaths = @(
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:LocalAppData\Microsoft\Edge\Application\msedge.exe"
    )
    $edgeExe = $edgePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

    if ($edgeExe) {
        $proc = Start-Process $edgeExe '--app=http://127.0.0.1:8401/' -WindowStyle Normal -PassThru
        if ($proc) {
            Save-LastBrowserPid -BrowserPid $proc.Id
        }
        return
    }

    Start-Process 'http://127.0.0.1:8401/'
}

function Test-NovaUiReachable {
    try {
        $resp = Invoke-WebRequest -Uri 'http://127.0.0.1:8401/' -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Ensure-NovaHosts {
    if (Test-NovaUiReachable) {
        return $true
    }

    try {
        $scriptDir = Split-Path -Parent $PSCommandPath
        $candidateRoots = @()
        $candidateRoots += (Resolve-Path (Join-Path $scriptDir '..\..')).Path
        $candidateRoots += (Resolve-Path (Join-Path $scriptDir '..\..\..')).Path
        $candidateRoots += 'C:\DMELOGIC MAIN'

        $startNovaBat = $null
        foreach ($root in $candidateRoots) {
            if (-not $root) { continue }
            $candidate = Join-Path $root 'start_nova.bat'
            if (Test-Path $candidate) {
                $startNovaBat = $candidate
                break
            }
        }

        if ($startNovaBat) {
            Start-Process 'cmd.exe' -ArgumentList @('/c', ('"{0}"' -f $startNovaBat)) -WindowStyle Minimized
            Add-Content -Path $logPath -Value "$(Get-Date -Format o) Nova hosts were down; launched start_nova.bat."

            for ($i = 0; $i -lt 30; $i++) {
                Start-Sleep -Milliseconds 500
                if (Test-NovaUiReachable) {
                    Add-Content -Path $logPath -Value "$(Get-Date -Format o) Nova UI became reachable after auto-start."
                    return $true
                }
            }
            Add-Content -Path $logPath -Value "$(Get-Date -Format o) Nova UI still unreachable after auto-start wait."
        } else {
            Add-Content -Path $logPath -Value "$(Get-Date -Format o) start_nova.bat not found; cannot auto-start Nova hosts."
        }
    } catch {
        Add-Content -Path $logPath -Value "$(Get-Date -Format o) Ensure-NovaHosts failed: $($_.Exception.Message)"
    }

    return (Test-NovaUiReachable)
}

function Invoke-WakeTrigger {
    try {
        $resp = Invoke-RestMethod -Uri 'http://127.0.0.1:8401/wake_trigger' -Method Post -TimeoutSec 2 -ErrorAction Stop
        return [int]($resp.clients_notified)
    } catch {
        return 0
    }
}

try {
    Add-Type -AssemblyName System.Speech

    $recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine
    $recognizer.SetInputToDefaultAudioDevice()

    $choices = New-Object System.Speech.Recognition.Choices
    $choices.Add('hey nova')
    $choices.Add('ok nova')
    $choices.Add('okay nova')
    $choices.Add('nova')

    $builder = New-Object System.Speech.Recognition.GrammarBuilder
    $builder.Culture = [System.Globalization.CultureInfo]::GetCultureInfo('en-US')
    $builder.Append($choices)

    $grammar = New-Object System.Speech.Recognition.Grammar($builder)
    $recognizer.LoadGrammar($grammar)

    Add-Content -Path $logPath -Value "$(Get-Date -Format o) Wake listener started."

    $lastWake = [datetime]::MinValue
    $cooldown = [timespan]::FromSeconds(8)

    while ($true) {
        try {
            $result = $recognizer.Recognize([timespan]::FromSeconds(1))
            if ($null -eq $result) { continue }

            $text = $result.Text.ToLowerInvariant().Trim()
            $confidence = [double]$result.Confidence
            if ($confidence -lt 0.72) { continue }

            $now = Get-Date
            if (($now - $lastWake) -lt $cooldown) { continue }
            $lastWake = $now

            Add-Content -Path $logPath -Value "$(Get-Date -Format o) Wake phrase heard: '$text' confidence=$confidence"

            Ensure-NovaHosts | Out-Null

            $notified = Invoke-WakeTrigger
            $windowExists = $null -ne (Find-NovaWindow)

            if ($notified -gt 0) {
                Focus-NovaWindow | Out-Null
                Add-Content -Path $logPath -Value "$(Get-Date -Format o) Triggered $notified client(s); focused window."
            } elseif ($windowExists) {
                Focus-NovaWindow | Out-Null
                Add-Content -Path $logPath -Value "$(Get-Date -Format o) Window exists but no websocket clients; focused existing window."
            } else {
                Open-NovaBrowser
                Add-Content -Path $logPath -Value "$(Get-Date -Format o) No Nova window found; opened browser."

                for ($i = 0; $i -lt 6; $i++) {
                    Start-Sleep -Milliseconds 800
                    $postOpen = Invoke-WakeTrigger
                    if ($postOpen -gt 0) {
                        Add-Content -Path $logPath -Value "$(Get-Date -Format o) Post-open wake trigger delivered to $postOpen client(s)."
                        break
                    }
                }
            }
        } catch {
            continue
        }
    }
}
catch {
    Add-Content -Path $logPath -Value "$(Get-Date -Format o) Wake listener failed to start: $($_.Exception.Message)"
}
finally {
    if ($null -ne $recognizer) {
        try { $recognizer.Dispose() } catch {}
    }
    if ($null -ne $mutex) {
        try { $mutex.ReleaseMutex() | Out-Null } catch {}
        try { $mutex.Dispose() } catch {}
    }
}

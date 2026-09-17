param(
    [ValidateSet('Open','Start','Status','Stop','InstallStartup','RemoveStartup')][string]$Action = 'Open',
    [string]$EnvFile
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$statePath = Join-Path $projectRoot 'data\local_process.json'
$launchPath = Join-Path $projectRoot 'data\local_launch.json'
$config = Get-Content -LiteralPath (Join-Path $projectRoot 'config\local.json') -Raw | ConvertFrom-Json
if ($config.host -ne '127.0.0.1' -or $config.port -lt 1 -or $config.port -gt 65535) { throw 'Invalid local address.' }
$localUrl = 'http://127.0.0.1:' + $config.port
$state = $null
$ownedProcess = $null
if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $candidate = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
    if ($candidate -and $candidate.StartTime.ToUniversalTime().Ticks.ToString() -eq $state.start_ticks) {
        $ownedProcess = $candidate
    }
}
if ($Action -eq 'Status') {
    @{ running = [bool]$ownedProcess; url = $localUrl; process = $state } | ConvertTo-Json -Depth 5
    exit 0
}
if ($Action -eq 'Stop') {
    if ($ownedProcess) { Stop-Process -Id $ownedProcess.Id }
    @{ stopped = [bool]$ownedProcess } | ConvertTo-Json
    exit 0
}
if ($EnvFile) {
    $resolvedEnv = (Resolve-Path -LiteralPath $EnvFile).Path
    if ($resolvedEnv.Contains('"') -or $resolvedEnv.Contains("`n")) { throw 'Unsupported credential-file path.' }
    @{ env_file = $resolvedEnv } | ConvertTo-Json | Set-Content -LiteralPath $launchPath -Encoding UTF8
} elseif (Test-Path -LiteralPath $launchPath) {
    $resolvedEnv = (Get-Content -LiteralPath $launchPath -Raw | ConvertFrom-Json).env_file
}
$startupPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Arbitrage V2 Local Desk.lnk'
if ($Action -eq 'RemoveStartup') {
    if (Test-Path -LiteralPath $startupPath) { Remove-Item -LiteralPath $startupPath }
    @{ startup_enabled = $false } | ConvertTo-Json
    exit 0
}
if ($Action -eq 'InstallStartup') {
    $shellObject = New-Object -ComObject WScript.Shell
    foreach ($entry in @(@{path=$startupPath;action='Start'},
        @{path=(Join-Path ([Environment]::GetFolderPath('Desktop')) 'Arbitrage V2 Local Desk.lnk');action='Open'})) {
        $shortcut = $shellObject.CreateShortcut($entry.path)
        $shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell1.0\powershell.exe'
        $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $PSCommandPath + '" -Action ' + $entry.action
        $shortcut.WorkingDirectory = $projectRoot
        $shortcut.WindowStyle = 7
        $shortcut.Description = 'Arbitrage V2 local webpage and background collector'
        $shortcut.Save()
    }
    @{ startup_enabled = $true; shortcut = $startupPath; url = $localUrl } | ConvertTo-Json
    exit 0
}
$mutex = New-Object Threading.Mutex($false, 'Local\ArbitrageV2LocalDeskStart')
$taken = $false
try {
    $taken = $mutex.WaitOne(10000)
    if (-not $taken) { throw 'Another launcher is starting the local desk.' }
    # Re-read after taking the launcher lock, because another launch may have finished.
    if (Test-Path -LiteralPath $statePath) {
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $candidate = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
        if ($candidate -and $candidate.StartTime.ToUniversalTime().Ticks.ToString() -eq $state.start_ticks) { $ownedProcess = $candidate }
    }
    if (-not $ownedProcess) {
        if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create the v2 Python environment first.' }
        $runKey = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8)
        $outputPath = Join-Path $projectRoot "data\local-$runKey.log"
        $errorPath = Join-Path $projectRoot "data\local-$runKey.err.log"
        $launchArguments = @('-B','-u','-m','arbitrage_v2','web')
        if ($resolvedEnv) { $launchArguments += @('--env-file',('"' + $resolvedEnv + '"')) }
        $ownedProcess = Start-Process -FilePath $pythonPath -ArgumentList $launchArguments `
            -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $outputPath -RedirectStandardError $errorPath
        $state = @{ pid = $ownedProcess.Id; start_ticks = $ownedProcess.StartTime.ToUniversalTime().Ticks.ToString();
            started_at = [DateTime]::UtcNow.ToString('o'); stdout = $outputPath; stderr = $errorPath; url = $localUrl }
        $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
        $ready = $false
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            Start-Sleep -Milliseconds 500
            $ownedProcess.Refresh()
            if ($ownedProcess.HasExited) { throw ('Local desk could not start. Read ' + $errorPath) }
            try {
                $session = Invoke-RestMethod -Uri ($localUrl + '/api/session') -TimeoutSec 1
                if ($session.token) { $ready = $true; break }
            } catch { }
        }
        if (-not $ready) { throw ('Local desk has not responded yet. Check ' + $errorPath) }
    }
    if ($Action -eq 'Open') { Start-Process $localUrl }
    @{ running = $true; url = $localUrl; process = $state } | ConvertTo-Json -Depth 5
} finally {
    if ($taken) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}

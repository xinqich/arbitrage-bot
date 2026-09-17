param(
    [ValidateSet('Start','Status','Stop')][string]$Action = 'Status',
    [string]$EnvFile,
    [ValidateRange(60,86400)][int]$IntervalSeconds = 21600
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$statePath = Join-Path $projectRoot 'data\collector_process.json'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
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
    @{ running = [bool]$ownedProcess; process = $state } | ConvertTo-Json -Depth 5
    exit 0
}
if ($Action -eq 'Stop') {
    if ($ownedProcess) { Stop-Process -Id $ownedProcess.Id }
    @{ stopped = [bool]$ownedProcess; process = $state } | ConvertTo-Json -Depth 5
    exit 0
}
if ($ownedProcess) { throw 'The managed collector is already running.' }
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create the v2 Python environment first.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'data\research.sqlite3'))) {
    throw 'Initialize the research journal first.'
}
$runKey = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8)
$outputPath = Join-Path $projectRoot "data\collector-$runKey.log"
$errorPath = Join-Path $projectRoot "data\collector-$runKey.err.log"
$launchArguments = @('-B','-u','-m','arbitrage_v2','collect','config\watchlist.json',
    '--watch','--interval-seconds',$IntervalSeconds.ToString(),
    '--screen-policy','config\research_assumptions.json')
if ($EnvFile) {
    $resolvedEnv = (Resolve-Path -LiteralPath $EnvFile).Path
    if ($resolvedEnv.Contains('"')) { throw 'Unsupported quote in credential-file path.' }
    $launchArguments += @('--env-file',('"' + $resolvedEnv + '"'))
}
$collectorProcess = Start-Process -FilePath $pythonPath -ArgumentList $launchArguments `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $outputPath -RedirectStandardError $errorPath
$state = @{ pid = $collectorProcess.Id; start_ticks = $collectorProcess.StartTime.ToUniversalTime().Ticks.ToString();
    started_at = [DateTime]::UtcNow.ToString('o'); interval_seconds = $IntervalSeconds;
    stdout = $outputPath; stderr = $errorPath; project = $projectRoot }
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding UTF8
$state | ConvertTo-Json -Depth 5

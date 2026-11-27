#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Arm or disarm the socket trace the hub's port watch reads after a Tcpip 4231/4266.

.DESCRIPTION
    The owner runs -Arm once in an elevated PowerShell. It starts a circular ETW
    session (Winsock AFD socket create, bind and close; TCPIP binds and port
    reservations; each event with its process id), capped at -MaxMB, and
    registers a scheduled task that runs as SYSTEM on each Tcpip 4231/4266 and at
    startup. The task (-Save) stops the session, moves its file into the trace
    folder as trace-<UTC stamp>.etl, keeps the newest -Keep saved files, and
    starts the session again. The hub only reads those saved files
    (src/health/port_trace.py); it never starts or stops the session.

    -Disarm stops the session, removes the task and the armed marker, and leaves
    the saved traces in place.

    Nothing here changes a port range, TcpTimedWaitDelay or any other machine
    setting.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\port_trace.ps1 -Arm
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\port_trace.ps1 -Disarm
#>
[CmdletBinding()]
param(
    [switch]$Arm,
    [switch]$Disarm,
    [switch]$Save,
    # Resolved once by -Arm, as the owner, and written into the task: under SYSTEM the hub's data root
    # would resolve against the wrong profile.
    [string]$TraceDir,
    [int]$MaxMB = 512,
    [int]$Keep = 5
)

$ErrorActionPreference = 'Stop'
$Session = 'LlmHubPortTrace'
$TaskName = 'LlmHubPortTraceSave'
$Logman = "$env:SystemRoot\System32\logman.exe"
$PowerShellExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Resolve-TraceDir {
    if ($TraceDir) { return $TraceDir }
    $python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { throw "No .venv at $RepoRoot; run setup first." }
    Push-Location $RepoRoot
    try { $dir = (& $python -m src.health.port_trace).Trim() } finally { Pop-Location }
    if (-not $dir) { throw 'The hub did not name a trace folder.' }
    return $dir
}

function Stop-Session {
    # A session that is not running is the normal state at startup and on a fresh arm, so its error is
    # expected; Windows PowerShell turns native stderr into a terminating error under 'Stop'.
    $ErrorActionPreference = 'Continue'
    & $Logman stop $Session -ets *> $null
}

function Start-Session([string]$dir) {
    $live = Join-Path $dir 'live.etl'
    & $Logman start $Session -ets -o $live -mode Circular -max $MaxMB -bs 1024 -nb 32 128 `
        -p 'Microsoft-Windows-Winsock-AFD' 0x4 4
    if ($LASTEXITCODE -ne 0) { throw "logman start exited $LASTEXITCODE" }
    # AFD sees TCP and UDP socket binds and closes; TCPIP keywords 0x8 (bind) and 0x80 (diagnosis) add
    # the port reservations AFD cannot see.
    & $Logman update trace $Session -ets -p 'Microsoft-Windows-TCPIP' 0x88 4
    if ($LASTEXITCODE -ne 0) { throw "logman update exited $LASTEXITCODE" }
}

function Save-Trace([string]$dir) {
    Stop-Session
    $live = Join-Path $dir 'live.etl'
    if (Test-Path $live) {
        $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
        Move-Item -LiteralPath $live -Destination (Join-Path $dir "trace-$stamp.etl") -Force
    }
    # Restart first, so the next burst is covered even if pruning fails.
    if (Test-Path (Join-Path $dir 'armed.json')) { Start-Session $dir }
    # A file the hub is reading right now cannot be removed; the next save prunes it.
    Get-ChildItem -LiteralPath $dir -Filter 'trace-*.etl' | Sort-Object Name -Descending |
        Select-Object -Skip $Keep | Remove-Item -Force -ErrorAction SilentlyContinue
}

function Register-SaveTask([string]$dir) {
    $class = Get-CimClass -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
    $onEvent = New-CimInstance -CimClass $class -ClientOnly
    $onEvent.Enabled = $true
    $onEvent.Subscription = '<QueryList><Query Id="0" Path="System"><Select Path="System">' +
        "*[System[Provider[@Name='Tcpip'] and (EventID=4231 or EventID=4266)]]" +
        '</Select></Query></QueryList>'
    $atStartup = New-ScheduledTaskTrigger -AtStartup
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Save -TraceDir `"$dir`" " +
        "-MaxMB $MaxMB -Keep $Keep"
    $action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $arguments
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances Queue -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName $TaskName -Trigger @($onEvent, $atStartup) -Action $action `
        -Principal $principal -Settings $settings -Force | Out-Null
}

if (@($Arm, $Disarm, $Save | Where-Object { $_ }).Count -ne 1) {
    throw 'Pass exactly one of -Arm, -Disarm or -Save.'
}

$dir = Resolve-TraceDir
if ($Save) {
    Save-Trace $dir
    return
}
if ($Arm) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $armedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    @{ armed_at = $armedAt; session = $Session; task = $TaskName; max_mb = $MaxMB } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dir 'armed.json') -Encoding UTF8
    Stop-Session
    Start-Session $dir
    Register-SaveTask $dir
    Write-Host "Armed: session $Session ($MaxMB MB circular), task $TaskName, traces in $dir"
    & $Logman query $Session -ets
    return
}
Stop-Session
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $dir 'armed.json') -Force -ErrorAction SilentlyContinue
Write-Host "Disarmed: session $Session stopped, task $TaskName removed; saved traces kept in $dir"

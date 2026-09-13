param(
    [Parameter(Mandatory = $true)][string]$Port,
    [ValidateSet('idle', 'single', 'double', 'matrix_single', 'completed', 'error', 'degraded', 'stale', 'stale_after_last_good', 'offline', 'offline_after_last_good', 'offline_cold_start', 'longtext')][string]$Scenario = 'single',
    [switch]$Live,
    [switch]$LiveDryRun,
    [int]$IntervalSeconds = 60,
    [int]$StreamDurationSeconds = 15,
    [switch]$Execute,
    [switch]$Rollback
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$bridge = Join-Path $root 'build\runboard\RunBoardBridge.exe'
$binary = Join-Path $root 'build\runboard\runboard-oabi'
$deploy = Join-Path $PSScriptRoot 'deploy-runboard.ps1'
$serialCommand = Join-Path $PSScriptRoot 'serial-command.ps1'

function Step([string]$message) { Write-Output ('[RunBoard] ' + $message) }
function Run-Checked([string]$file, [string[]]$arguments) {
    & $file @arguments
    if ($LASTEXITCODE -ne 0) { throw "command failed with exit code ${LASTEXITCODE}: $file" }
}
function Serial-Checked([string]$command, [int]$seconds = 3) {
    Step "serial read-only/control: $command"
    & $serialCommand -Port $Port -Command $command -ReadSeconds $seconds
    if ($LASTEXITCODE -ne 0) { throw "serial command failed: $command" }
}

if ($Execute -and $Rollback) { throw 'Use either -Execute or -Rollback, not both.' }
if ($LiveDryRun -and ($Execute -or $Rollback)) { throw 'LiveDryRun is offline-only; do not combine it with Execute or Rollback.' }
if ($IntervalSeconds -lt 1 -or $StreamDurationSeconds -lt 1) { throw 'Interval and duration must be positive.' }

Step "port is explicit: $Port"
Step 'no Flash/rootfs/startup change is performed by this script'
if (-not (Test-Path -LiteralPath $bridge -PathType Leaf)) { throw "Bridge not found: $bridge" }
if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) { throw "Target artifact not found: $binary" }

if ($LiveDryRun) {
    Step 'PERSISTENT LIVE DRY RUN: reuse providers across cycles, verify RB1, do not open serial'
    & python (Join-Path $root 'apps\runboard\host\runboard_state_cli.py') '--live' '--persistent-dry-run' '--cycles' '2' '--interval' '1'
    if ($LASTEXITCODE -ne 0) { throw "live dry-run failed with exit code ${LASTEXITCODE}" }
    Step 'LIVE_DRY_RUN_COMPLETED=YES'
    exit 0
}

if (-not $Execute -and -not $Rollback) {
    Step 'PLAN ONLY: no serial port is opened and no board command is sent'
    $plannedMode = if ($Live) { 'live' } else { $Scenario }
    Step "would run bridge self-test, temporary deploy, $plannedMode stream, explicit stop, status/Qtopia check"
    Step 'rollback is a separate explicit -Rollback invocation'
    exit 0
}

if ($Rollback) {
    Step 'ROLLBACK: stop is attempted once through the existing RunBoard Bridge'
    Run-Checked $bridge @('board', 'stop', '--application', '--port', $Port)
    Serial-Checked '/opt/jz2440/bin/appctl qtopia' 4
    Serial-Checked 'rm -f /opt/jz2440/apps/runboard/app /opt/jz2440/apps/runboard/app.conf; rmdir /opt/jz2440/apps/runboard' 3
    Serial-Checked '/opt/jz2440/bin/appctl status' 3
    Serial-Checked '/opt/jz2440/bin/appctl list' 3
    Step 'ROLLBACK_COMPLETED=YES'
    exit 0
}

Step 'local bridge self-test'
Run-Checked $bridge @('--self-test')
Step 'temporary deployment through deploy-runboard.ps1'
Run-Checked 'powershell' @('-ExecutionPolicy', 'Bypass', '-File', $deploy, '-Port', $Port, '-Deploy')

$modeArguments = if ($Live) { @('--live') } else { @('--scenario', $Scenario) }
$runMode = if ($Live) { 'live' } else { $Scenario }
Step "start and stream $runMode data for $StreamDurationSeconds seconds"
Run-Checked $bridge (@('board', 'start', '--console') + $modeArguments + @('--interval', $IntervalSeconds, '--duration', $StreamDurationSeconds, '--port', $Port))

Step 'stop once and require APPSTOP/RC=0'
Run-Checked $bridge @('board', 'stop', '--application', '--port', $Port)
Serial-Checked '/opt/jz2440/bin/appctl status' 3
Serial-Checked '/opt/jz2440/bin/appctl list' 3
Step 'MANUAL_CHECK_REQUIRED=LCD visual result and physical serial reconnect'
Step 'TEMPORARY_ACCEPTANCE_COMPLETED=YES'

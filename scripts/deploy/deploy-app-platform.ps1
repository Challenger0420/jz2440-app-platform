param(
    [Parameter(Mandatory = $true)]
    [string]$Port,
    [string]$App = 'codex-monitor',
    [string]$Binary = 'build\status-header-right\codex-monitor-oabi',
    [switch]$Deploy
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$registry = Join-Path $root "apps\codex-monitor"
$appctl = Join-Path $root 'platform\board\appctl\appctl'
$upload = Join-Path $root 'scripts\deploy\upload-uue.ps1'
$binaryPath = Join-Path $root $Binary

if (-not (Test-Path -LiteralPath $binaryPath -PathType Leaf)) {
    throw "Target binary not found: $binaryPath"
}
if (-not (Test-Path -LiteralPath (Join-Path $registry 'app.conf') -PathType Leaf)) {
    throw "App registration not found: $registry\app.conf"
}

$bytes = [IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $binaryPath))
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $binaryPath).Hash
Write-Output "APP=$App"
Write-Output ("LOCAL_SIZE={0}" -f $bytes.Length)
Write-Output "LOCAL_SHA256=$hash"
Write-Output 'TARGET_ROOT=/opt/jz2440'
Write-Output 'TARGET_REGISTRY=/opt/jz2440/apps'
Write-Output 'TARGET_RUNTIME=/tmp/jz2440'

if (-not $Deploy) {
    Write-Output 'PLAN_ONLY=YES'
    Write-Output 'No board command was sent. Re-run with -Deploy during an onsite maintenance window.'
    exit 0
}

Write-Warning 'Deploying the generic application platform to the board.'
& $upload -Port $Port -InputPath $appctl -TargetPath '/tmp/jz2440-appctl'
& $upload -Port $Port -InputPath $binaryPath -TargetPath "/tmp/jz2440-$App"
& $upload -Port $Port -InputPath (Join-Path $registry 'app.conf') -TargetPath "/tmp/jz2440-$App.conf"

$commands = @(
    'mkdir -p /opt/jz2440/bin /opt/jz2440/apps/' + $App,
    'cp /tmp/jz2440-appctl /opt/jz2440/bin/appctl',
    'cp /tmp/jz2440-' + $App + ' /opt/jz2440/apps/' + $App + '/app',
    'cp /tmp/jz2440-' + $App + '.conf /opt/jz2440/apps/' + $App + '/app.conf',
    'chmod 755 /opt/jz2440/bin/appctl /opt/jz2440/apps/' + $App + '/app',
    'sh -n /opt/jz2440/bin/appctl',
    '/opt/jz2440/bin/appctl list',
    '/opt/jz2440/bin/appctl status'
)
foreach ($command in $commands) {
    & (Join-Path $PSScriptRoot 'serial-command.ps1') -Port $Port -Command $command -ReadSeconds 2
}
Write-Output 'DEPLOY_COMPLETED=YES'

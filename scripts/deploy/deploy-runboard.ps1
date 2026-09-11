param(
    [Parameter(Mandatory=$true)][string]$Port,
    [string]$Binary = "build\runboard\runboard-oabi",
    [switch]$Deploy
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$binaryPath = Join-Path $root $Binary
$confPath = Join-Path $root "apps\runboard\app.conf"
if (-not (Test-Path -LiteralPath $binaryPath -PathType Leaf)) { throw "RunBoard target binary not found: $binaryPath" }
if (-not (Test-Path -LiteralPath $confPath -PathType Leaf)) { throw "RunBoard app registration not found: $confPath" }
$bytes = [IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $binaryPath))
Write-Output "APP=runboard"
Write-Output ("LOCAL_SIZE={0}" -f $bytes.Length)
Write-Output ("LOCAL_SHA256={0}" -f (Get-FileHash -Algorithm SHA256 -LiteralPath $binaryPath).Hash)
Write-Output "TARGET_APP=/opt/jz2440/apps/runboard/app"
Write-Output "TARGET_CONF=/opt/jz2440/apps/runboard/app.conf"
Write-Output "RUNTIME_STATE=/tmp/jz2440"
if (-not $Deploy) {
    Write-Output "PLAN_ONLY=YES"
    Write-Output "No board command was sent. Use -Deploy only for temporary volatile deployment."
    exit 0
}

& (Join-Path $PSScriptRoot "upload-uue.ps1") -Port $Port -InputPath $binaryPath -TargetPath "/tmp/jz2440-runboard"
& (Join-Path $PSScriptRoot "upload-uue.ps1") -Port $Port -InputPath $confPath -TargetPath "/tmp/jz2440-runboard.conf"
$commands = @(
    "mkdir -p /opt/jz2440/apps/runboard",
    "cp /tmp/jz2440-runboard /opt/jz2440/apps/runboard/app",
    "cp /tmp/jz2440-runboard.conf /opt/jz2440/apps/runboard/app.conf",
    "chmod 755 /opt/jz2440/apps/runboard/app",
    "/opt/jz2440/bin/appctl list",
    "/opt/jz2440/bin/appctl status"
)
foreach ($command in $commands) {
    & (Join-Path $PSScriptRoot "serial-command.ps1") -Port $Port -Command $command -ReadSeconds 2
}
Write-Output "DEPLOY_COMPLETED=YES"

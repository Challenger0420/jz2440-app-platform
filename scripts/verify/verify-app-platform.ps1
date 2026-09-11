param(
    [Parameter(Mandatory = $true)]
    [string]$Port
)

$ErrorActionPreference = 'Stop'
$serialCommand = Join-Path $PSScriptRoot '..\deploy\serial-command.ps1'

Write-Output 'This is an onsite verification helper; it is not run automatically.'
Write-Output 'It does not change boot files or enable autostart.'
Write-Output "PORT=$Port"

$checks = @(
    'echo APP_PLATFORM_CONSOLE_SYNC',
    '/opt/jz2440/bin/appctl list',
    '/opt/jz2440/bin/appctl status'
)
foreach ($command in $checks) {
    & $serialCommand -Port $Port -Command $command -ReadSeconds 2
}

Write-Output 'Manual-only checks still required onsite:'
Write-Output '  appctl start codex-monitor; verify APPREADY and CQMREQ'
Write-Output '  stop through the host CQMQUIT path; verify APPSTOP and Qtopia recovery'
Write-Output '  console/application ownership handoff and physical USB reconnect'
Write-Output 'VERIFY_COMPLETED=SOFTWARE_COMMANDS_ONLY'

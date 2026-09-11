param(
    [Parameter(Mandatory = $true)]
    [string]$Port,
    [Parameter(Mandatory = $true)]
    [string]$Command,
    [int]$ReadSeconds = 2
)

$ErrorActionPreference = 'Stop'
$serial = New-Object IO.Ports.SerialPort($Port, 115200, [IO.Ports.Parity]::None, 8, [IO.Ports.StopBits]::One)
$serial.Handshake = [IO.Ports.Handshake]::None
$serial.DtrEnable = $false
$serial.RtsEnable = $false
$serial.ReadTimeout = 100
$serial.Open()
try {
    $serial.Write($Command)
    $serial.Write([char]13)
    $deadline = (Get-Date).AddSeconds($ReadSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($serial.BytesToRead -gt 0) { Write-Output $serial.ReadExisting() }
        Start-Sleep -Milliseconds 100
    }
    if ($serial.BytesToRead -gt 0) { Write-Output $serial.ReadExisting() }
}
finally {
    $serial.Close()
    $serial.Dispose()
}

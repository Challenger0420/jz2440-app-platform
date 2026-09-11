param(
    [Parameter(Mandatory = $true)]
    [string]$Port,
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [Parameter(Mandatory = $true)]
    [string]$TargetPath
)

$ErrorActionPreference = 'Stop'
if ($TargetPath -notmatch '^/tmp/[A-Za-z0-9._-]+$') {
    throw 'This helper only permits upload targets directly below /tmp.'
}

function ConvertTo-UuChar([int]$value) {
    $value = $value -band 0x3f
    if ($value -eq 0) { return [char]96 }
    return [char](32 + $value)
}

function ConvertTo-UuLines([byte[]]$bytes) {
    $lines = New-Object System.Collections.Generic.List[string]
    for ($offset = 0; $offset -lt $bytes.Length; $offset += 45) {
        $count = [Math]::Min(45, $bytes.Length - $offset)
        $line = [Text.StringBuilder]::new()
        [void]$line.Append((ConvertTo-UuChar $count))
        for ($i = 0; $i -lt $count; $i += 3) {
            $a = $bytes[$offset + $i]
            $b = if ($i + 1 -lt $count) { $bytes[$offset + $i + 1] } else { 0 }
            $c = if ($i + 2 -lt $count) { $bytes[$offset + $i + 2] } else { 0 }
            [void]$line.Append((ConvertTo-UuChar ($a -shr 2)))
            [void]$line.Append((ConvertTo-UuChar (($a -shl 4) -bor ($b -shr 4))))
            [void]$line.Append((ConvertTo-UuChar (($b -shl 2) -bor ($c -shr 6))))
            [void]$line.Append((ConvertTo-UuChar $c))
        }
        [void]$lines.Add($line.ToString())
    }
    return $lines
}

$bytes = [IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $InputPath))
$lines = ConvertTo-UuLines $bytes
$serial = New-Object IO.Ports.SerialPort($Port, 115200, [IO.Ports.Parity]::None, 8, [IO.Ports.StopBits]::One)
$serial.Handshake = [IO.Ports.Handshake]::None
$serial.DtrEnable = $false
$serial.RtsEnable = $false
$serial.NewLine = "`n"
$serial.Open()
try {
    $serial.Write("uudecode -o $TargetPath")
    $serial.Write([char]13)
    Start-Sleep -Milliseconds 500
    $serial.Write("begin 755 upload")
    $serial.Write([char]13)
    foreach ($line in $lines) {
        $serial.Write($line)
        $serial.Write([char]13)
        Start-Sleep -Milliseconds 20
    }
    $serial.Write('`')
    $serial.Write([char]13)
    $serial.Write('end')
    $serial.Write([char]13)
    Start-Sleep -Milliseconds 700
}
finally {
    $serial.Close()
    $serial.Dispose()
}
Write-Output ("uploaded {0} bytes to {1}" -f $bytes.Length, $TargetPath)

# Codex Monitor

Codex Monitor is the first application registered on the generic JZ2440 App
Platform. It displays real Codex quota data on the 480x272 RGB565 framebuffer
and is launched as a foreground application through `platform/board/appctl`.
The UI reference image is [`docs/images/ui-preview.png`](docs/images/ui-preview.png).

## Application contents

- `target/src/codex_monitor.c`: Codex-specific framebuffer UI, serial backend, status state, and quota rendering.
- `target/include/`: generated glyph data and frozen UI layout constants.
- `protocol/`: CQMREQ, CQM1, CQMQUIT parser/encoder and request-driven session tests.
- `host/`: Codex Provider, quota model, application session, and provider tests.
- `tools/`: preview, font, color, and status-logic tools.
- `app.conf`: registry entry for `/opt/jz2440/apps/codex-monitor/app`.

## Protocol

The board requests data and the host responds:

```text
Board → Host: <CQMREQ|V=1>
Host  → Board: <CQM1|...>
Host  → Board: <CQMQUIT>
```

Without a recent CQMREQ, the host sends no quota frame. The UI retains the
last valid quota while reporting `WAIT`, `LIVE`, or `STALE`; `UPDATED ... AGO`
uses the monitor's local receive age.

## Target build

The target remains Samsung S3C2440A / ARM920T / ARMv4T on Linux 2.6.22.6:

- freestanding custom OABI syscall runtime;
- no target libc or dynamic linker;
- `-mabi=apcs-gnu`, `-nostdlib`, and soft-float;
- framebuffer `/dev/fb0`, 480x272, RGB565;
- direct serial `/dev/s3c2410_serial0`, 115200 8N1, no flow control.

Build with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build\build-target.ps1
python apps\codex-monitor\tools\preview.py
```

The standalone Bridge provider can be checked without opening a board serial
port. Run the bridge build script, then the standard executable with
--provider-trace and --provider-test. The provider launches codex.exe directly
with app-server --stdio, sends newline-delimited UTF-8 JSON, and drains stderr
separately from the JSON stdout stream. --provider-test performs five real
provider-only checks and does not open a board COM port.

## Final validation

Final validated target: 51400 bytes, SHA256
8C2C20DB7E2C1217B385E4BADADDF1B9769C9C7C2EC96ED6FA26290EBE92EA35.
The standard EXE completed a real three-frame hardware request/response
acceptance on the locally selected USB serial port, including APPREADY, CQMREQ, real Provider, and CQM1
responses. Board Controller start/stop and Qtopia recovery passed. Codex
Monitor v1 is finalized and feature-frozen; future changes should be
explicit bug fixes.

## Current validation

The final validated target is 51400 bytes, SHA256
`8C2C20DB7E2C1217B385E4BADADDF1B9769C9C7C2EC96ED6FA26290EBE92EA35`.
The standard EXE completed the real three-frame hardware request/response
acceptance on the locally selected USB serial port, including Board Controller start/stop and Qtopia
recovery. UART ownership, USB reconnect, and final visual confirmation are
complete. Codex Monitor v1 is finalized and feature-frozen; board and
Windows autostart remain disabled/deferred.

# RunBoard

RunBoard is the Windows Host / JZ2440 display path for read-only research
server status. The Host collects a validated state, encodes RB1 v1, and the
board target only parses and renders that state.

Live scheduling defaults are one RB1/LCD publish and one Codex quota poll per
60 seconds. Server resources, experiments, and matrix/process metadata share
one read-only Server collection every 600 seconds; intervening frames reuse the
last successful Server snapshot and advance its freshness age.

## Local verification

```powershell
python apps/runboard/host/runboard_state_cli.py --scenario idle
python apps/runboard/host/runboard_state_cli.py --scenario single
python apps/runboard/host/runboard_state_cli.py --scenario double
python apps/runboard/host/runboard_state_cli.py --scenario matrix_single
python apps/runboard/host/runboard_state_cli.py --scenario stale
python apps/runboard/host/runboard_state_cli.py --scenario offline
python apps/runboard/host/runboard_state_cli.py --scenario offline_after_last_good
python apps/runboard/host/runboard_state_cli.py --scenario offline_cold_start
python apps/runboard/host/runboard_state_cli.py --scenario longtext
python apps/runboard/host/runboard_state_cli.py --live --dry-run
python apps/runboard/host/runboard_state_cli.py --live --persistent-dry-run --cycles 3 --interval 60
python apps/runboard/preview/runboard_preview.py --live
```

`--live --dry-run` reads the configured server and the accepted Codex Monitor
quota provider, validates the aggregated state, performs RB1 encode/decode and
prints only a sanitized summary. It never opens a serial port.
`--live --persistent-dry-run` performs several cycles with one persistent
Provider/Aggregator instance, so last-good values and freshness age can be
observed without serial I/O. Regular `--live` remains the one-shot diagnostic
frame mode for compatibility/debugging; the Bridge's live mode uses the
long-lived `--live-worker` protocol.

`matrix_single` is a safe local fixture for a matrix job: `MATRIX 7/45`,
`CELL 8/45`, and the current cell's `ROUND 37/50` are separate values. It does
not query a server.

`offline_after_last_good` models a server that is offline after a valid server
snapshot: the board shows the server snapshot age (for example, `UPDATED 3M
AGO`) while Codex quota remains independently fresh. `offline_cold_start`
models an offline server with no last-good snapshot and therefore displays
`UPDATED --` rather than fabricating an age. Neither fixture opens a serial
port.

## Temporary board path

Build the independent target and bridge:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build/build-runboard-target.ps1
powershell -ExecutionPolicy Bypass -File scripts/build/build-runboard-bridge.ps1
```

Inspect deployment without touching the board:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/deploy/deploy-runboard.ps1 -Port COMx
```

The deploy script is plan-only unless `-Deploy` is supplied. The accepted
platform installation lives under `/opt/jz2440/apps/runboard` and uses the
existing `appctl`; it does not write Flash, rootfs images, or startup scripts.
The host bridge owns COM discovery/transport and streams RB1 frames:

```powershell
build/runboard/RunBoardBridge.exe board start --console --scenario idle --duration 30 --port COMx
build/runboard/RunBoardBridge.exe board start --console --scenario longtext --duration 30 --port COMx
build/runboard/RunBoardBridge.exe board start --console --live --interval 60 --port COMx
build/runboard/RunBoardBridge.exe board stop --application --port COMx
```

The live Bridge starts one `runboard_state_cli.py --live-worker` process for
the whole session and sends sequence requests to it. The worker creates the
real providers and `RunBoardAggregator` once, then emits one complete RB1
frame per request. Mock scenarios continue to use the one-shot CLI path.

Use the actual local COM port only on the command line; no tracked script has a
fixed COM default. The offline acceptance wrapper is plan-only by default:

```powershell
scripts\deploy\accept-runboard.ps1 -Port COMx
scripts\deploy\accept-runboard.ps1 -Port COMx -LiveDryRun
scripts\deploy\accept-runboard.ps1 -Port COMx -Scenario single -Execute
scripts\deploy\accept-runboard.ps1 -Port COMx -Rollback
```

`-LiveDryRun` reads the live providers and verifies RB1 without opening the port.
The wrapper stops on the first error, requires `APPSTOP|runboard|RC=0` for an
executed board flow, and does not perform automatic recovery or permanent
deployment. LCD visual inspection, physical reconnect, and Qtopia state after
a real stop remain on-site checks.

## Final platform installation and hardware regression record

The final target is installed at the standard platform application path and is
registered with `appctl`. It persists across Reset but is not an autostart item;
the default post-Reset state remains Qtopia. The installed target is 44876 bytes
with SHA256
`917F8028CCD6922D2F41D82B83F840FCF4CAD81ECA8371B9FE77F2F20E9C8B66`.

The final host-to-target path passed APPREADY, continuous live RB1 frames,
target length/CRC/parse/draw diagnostics, normal APPSTOP, shell/termios/Qtopia
recovery, Codex Monitor regression, RunBoard-after-Codex, and one physical
USB-serial reconnect. The user confirmed the real Formal v6 live LCD and all
final mock state pages, including double, IDLE, STALE, OFFLINE, COMPLETED,
ERROR, DEGRADED, and longtext. The final board state is clean Qtopia with no
RunBoard or Codex Monitor process.

RunBoard and Codex Monitor are platform-managed applications. Future switching
belongs to Windows Control and must use `appctl` plus APPREADY/APPSTOP; it must
not directly kill processes, write the LCD, or bypass the board lifecycle.

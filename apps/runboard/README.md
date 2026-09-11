# RunBoard

RunBoard is the Windows Host / JZ2440 display path for read-only research
server status. The Host collects a validated state, encodes RB1 v1, and the
board target only parses and renders that state.

## Local verification

```powershell
python apps/runboard/host/runboard_state_cli.py --scenario idle
python apps/runboard/host/runboard_state_cli.py --scenario single
python apps/runboard/host/runboard_state_cli.py --scenario double
python apps/runboard/preview/runboard_preview.py --live
```

`--live` reads the configured server and reuses the accepted Codex Monitor
quota bridge in dry-run mode. It does not open a serial port from Python.

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

The deploy script is plan-only unless `-Deploy` is supplied. A deployment is
volatile under `/opt/jz2440/apps/runboard` and uses the existing `appctl`; it
does not write Flash, rootfs images, or startup scripts. The host bridge owns
COM discovery/transport and streams RB1 frames:

```powershell
build/runboard/RunBoardBridge.exe board start --console --scenario idle --duration 30 --port COMx
build/runboard/RunBoardBridge.exe board start --console --live --interval 10 --port COMx
build/runboard/RunBoardBridge.exe board stop --application --port COMx
```

Use the actual local COM port only on the command line; no tracked script has a
fixed COM default. The offline acceptance wrapper is plan-only by default:

```powershell
scripts\deploy\accept-runboard.ps1 -Port COMx
scripts\deploy\accept-runboard.ps1 -Port COMx -Scenario single -Execute
scripts\deploy\accept-runboard.ps1 -Port COMx -Rollback
```

The wrapper stops on the first error, requires `APPSTOP|runboard|RC=0`, and does
not perform automatic recovery or permanent deployment. LCD visual inspection,
physical reconnect, and Qtopia state after a real stop remain on-site checks.

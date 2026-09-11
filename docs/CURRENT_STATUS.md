# Current status / handoff

```text
Project direction: Generic JZ2440 Application Platform
Implemented app: Codex Monitor v1
Reusable platform: appctl, Board Controller, serial transport, target runtime
GitHub repository: Challenger0420/jz2440-app-platform
```

## Final Codex Monitor target

```text
Size: 51400 bytes
SHA256: 8C2C20DB7E2C1217B385E4BADADDF1B9769C9C7C2EC96ED6FA26290EBE92EA35
Target: S3C2440A / ARM920T / ARMv4T / Linux 2.6.22.6
ABI: freestanding custom OABI runtime, no libc or dynamic linker
```

The post-restructure clean build from the current working tree produced the
same 51400-byte artifact as the existing `build/status-header-right` reference:

```text
Structural rebuild SHA256: 8C2C20DB7E2C1217B385E4BADADDF1B9769C9C7C2EC96ED6FA26290EBE92EA35
Reproducibility: exact across two clean builds
Standard EXE real hardware E2E: PASS, 3 consecutive frames on the locally selected USB serial port
Codex Monitor: FINALIZED, FEATURE FREEZE
```


## Safe board baseline

```text
Qtopia: running
Monitor: stopped
Boot chain: unmodified
Persistent /opt/codex-monitor baseline: retained
```

This restructuring did not access the board or change `/opt`, Qtopia,
boot/init files, U-Boot, kernel, bootargs, NAND, or Windows autostart.

## Completed software gates

```text
APP_PLATFORM_DESIGN_GATE=PASS
APPCTL_IMPLEMENTATION_GATE=PASS
APPCTL_STATIC_COMPAT_GATE=PASS
BOARD_CONTROLLER_STATE_MACHINE_GATE=PASS
BOARD_CONTROLLER_SIMULATION_GATE=PASS
CODEX_ADAPTER_GATE=PASS
REQUEST_RESPONSE_PROTOCOL_GATE=PASS
HOST_TX_SILENCE_GATE=PASS
STATUS_UI_PREVIEW_GATE=PASS
STATUS_BUILD_GATE=PASS
DEPLOYMENT_SCRIPT_PREP_GATE=PASS
STANDARD_EXE_PROVIDER_GATE=PASS
PROVIDER_TRANSPORT_CONTRACT_GATE=PASS
STANDARD_EXE_CONTROLLER_SMOKE_GATE=PASS (no-serial simulation)
STANDARD_EXE_3_FRAME_GATE=PASS
STANDARD_EXE_STOP_GATE=PASS
BOARD_CONTROLLER_START_GATE=PASS
BOARD_CONTROLLER_STOP_GATE=PASS
CODEX_MONITOR_FINAL_GATE=PASS
CODEX_MONITOR_FEATURE_FREEZE=YES
```

The standalone Bridge provider path was validated with the standard EXE, not
through a PowerShell wrapper: five consecutive real provider-only attempts
completed successfully. The transport uses the resolved Codex executable,
newline-delimited UTF-8 JSON, and a separately drained stderr stream. Local
framing/encoding/malformed-frame contract tests also pass. No serial port was
opened during this provider-only validation.

## Pending physical gates

```text
APP_UART_OWNERSHIP_HARDWARE_GATE=PASS
APP_LAUNCHER_BOARD_GATE=PASS
BOARD_CONTROLLER_HARDWARE_GATE=PASS
STATUS_HARDWARE_GATE=PASS
USB_RECONNECT_FINAL_GATE=PASS
WINDOWS_AUTOSTART_GATE=DEFERRED
```

## Rejected design

Board autostart through `rcS` was rolled back and rejected because the legacy
`askfirst` shell and a foreground Monitor compete for UART RX. Historical
scripts and the rollback note are retained under
`docs/history/rejected-board-autostart/`; they are not an active deployment
path.

## Git rule

The repository identity was finalized and pushed to `main`. Future commits
and pushes still require explicit user instruction.

## Handoff

Start with `docs/architecture/OVERVIEW.md` and
`docs/architecture/REPOSITORY_LAYOUT.md`. Codex Monitor-specific details are
in `apps/codex-monitor/README.md`.

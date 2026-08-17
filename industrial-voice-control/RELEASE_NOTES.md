# Release Notes: v2.6.0-open-source

Released for the robot competition codebase as a safe, reusable reference implementation.

## Highlights

- Local Whisper recognition, Qwen intent handling, Chinese speech feedback and Modbus TCP integration.
- Recognition confidence, signal-to-noise, cancellation and whitelist safety gates.
- Single-write confirmation with no automatic retry after timeout or ambiguous state.
- Mouse and voice commands share the same validation and mutex path.
- Engineer diagnostics with per-session logs, search, filters, device status and light/dark/system themes.

## Safe Open-Source Defaults

- Robot control is disabled by default with `ROBOT_CONTROL_ENABLED=false`.
- The public configuration targets loopback only and uses example register `R40001`.
- Five generic example actions replace all production motion names and mappings.
- `robot_config.local.env` is ignored and parsed as a six-key whitelist; it is never executed as a script.
- No production addresses, registers, motion maps, logs, deployment reports, screenshots or historical backups are included.

## Verification

- `56/56` Python tests passed.
- Python compilation and front-end JavaScript syntax checks passed.
- Read-only deployment check completed with `FAIL=0`; the only environment warning was an Ollama version difference from the documented baseline.
- Desktop and 390px mobile views were checked with controls locked by default and no horizontal overflow.

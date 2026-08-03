# Changelog

All notable changes to Agent Trace Workbench appear in this file.

The version format follows a release cycle. A release adds one coherent capability to the workbench.

## 0.3.0 - 2026-08-03

### Added

- Configurable replay handlers from a local JSON file.
- Local Python handler scripts with a `run(arguments)` function.
- Fixed result stubs for tools without a script.
- Side-effect guard with `strict`, `local`, and `all` replay policies.
- Guarded step reporting in replay reports.
- `--config` and `--policy` options on the `replay` command.
- Environment configuration for the server via `ATW_HANDLERS_CONFIG` and `ATW_REPLAY_POLICY`.
- Deterministic tests for handler config, side-effect guards, CLI routes, and lockfile sync.
- Python 3.13 in the CI matrix.

### Changed

- Version numbers moved to 0.3.0.
- The replay report now includes the active policy and per-step guard state.
- The replay page shows guarded steps and the active policy.

## 0.2.0 - 2026-08-02

### Added

- Polling directory watcher for local JSON trace folders.
- Stable schema error reports that do not block valid files.
- Watch options for interval, pattern, and one-shot scans.

## 0.1.0 - 2026-08-02

### Added

- Portable JSON trace contract with Pydantic validation.
- SQLite trace store with idempotent ingestion.
- Deterministic replay with recorded-result fallback.
- Run comparison by ordered tool calls.
- FastAPI interface and JSON API.
- Local OpenTelemetry spans for workbench operations.

# Changelog

All notable changes to Agent Trace Workbench appear in this file.

The version format follows a release cycle. A release adds one coherent capability to the workbench.

## 0.6.0 - 2026-08-03

### Added

- WAL journal mode for the SQLite store so readers keep a committed snapshot.
- A busy timeout on every store connection so writers wait for the write lock.
- A retry path for write operations that report a transient database lock.
- `atw store` command that prints the active store configuration.
- `GET /api/store` route that exposes the store configuration.
- `ATW_DB_BUSY_TIMEOUT_MS` environment variable for the server.
- A live store status line in the page footer.
- Deterministic tests for WAL mode, retry behavior, shared-database use, the CLI command, and the API route.

### Changed

- Version numbers moved to 0.6.0.
- The architecture now includes SQLite coordination for shared databases.
- The run and dashboard pages report the active journal mode and busy timeout.

## 0.5.0 - 2026-08-03

### Added

- OTLP JSON import through the CLI and the `/api/otlp/traces` route.
- OTLP JSON export through the CLI and the `/api/runs/{run_id}/export` route.
- Portable JSON export files for one run or every run.
- Export links on the run detail page.
- `atw import-otlp` and `atw export` commands on the CLI.
- A lossless round trip between workbench runs and the OTLP JSON encoding.
- Typed OTLP attribute conversion with arrays, maps, and numeric values.
- Deterministic tests for OTLP conversion, import, export, and the API routes.

### Changed

- Version numbers moved to 0.5.0.
- The run detail page exposes JSON and OTLP downloads.
- The architecture now includes the OTLP import and export layer.

## 0.4.0 - 2026-08-03

### Added

- Text search across runs, spans, and tool calls.
- Span filtering by kind, status, and tool on the run page.
- Saved comparisons with create, list, get, and delete routes.
- Search box on the dashboard.
- Filter controls on the run detail page.
- Saved comparison list on the compare page.
- `atw search` command on the CLI.
- `atw comparisons` command on the CLI.
- Deterministic tests for search, filtering, and saved comparisons.

### Changed

- Version numbers moved to 0.4.0.
- `GET /api/runs` accepts a `q` query parameter.
- `GET /api/runs/{run_id}` accepts `kind`, `status`, and `tool` query parameters.
- The dashboard keeps its search query in the URL.

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

# Changelog

All notable changes to Agent Trace Workbench appear in this file.

The version format follows a release cycle. A release adds one coherent capability to the workbench.

## 1.11.0 - 2026-08-04

### Added

- Ordered failed-span summaries for runs in the dashboard day drill-down.
- Error summary fields in the day drill-down API and CLI JSON output.
- Failed-span count and messages in the day CSV export.
- Deterministic tests for storage, API, dashboard, CLI, and CSV summaries.

### Changed

- Version numbers moved to 1.11.0.
- Day cards show the failure count and first failure message.
- The day drill-down reuses the error timeline failure rule.

## 1.10.0 - 2026-08-04

### Added

- A span detail panel on the run-level error timeline.
- An Inspect button on each timeline event row that opens the full span record.
- Clickable timeline markers that open the same span detail panel.
- A detail panel that shows the span kind, status, offsets, duration, attributes, and tool call.
- The detail panel shows the recorded arguments, result, outcome, and failure message.
- A link in the detail panel that jumps to the matching span in the trace waterfall.
- `GET /api/runs/{run_id}/spans/{span_id}` route that returns the full span record for scripts.
- `atw span <run_id> <span_id>` command that prints the same span detail.
- Deterministic tests for the span detail, the API route, the CLI command, and the run page panel.

### Changed

- Version numbers moved to 1.10.0.
- The error timeline rows now carry an Inspect action beside the waterfall jump.
- The timeline markers respond to click and keyboard focus.
- The architecture now includes a span detail layer beside the run-level error timeline.

## 1.9.0 - 2026-08-04

### Added

- A run-level error timeline on the run detail page.
- A horizontal time axis that marks each failed span at its offset from the run start.
- A marker chart that shows when the failures happened inside one run.
- An event list below the chart with each span name, kind, offset, and failure message.
- A clickable event row that jumps to the matching span in the trace waterfall.
- `GET /api/runs/{run_id}/timeline` route that returns the failed spans for scripts.
- `GET /api/runs/{run_id}/timeline?format=csv` route that returns the events as a CSV attachment.
- `atw timeline <run_id>` command that prints the error timeline.
- `atw timeline <run_id> --format csv` command that prints the events as CSV.
- Run page JSON and CSV download links for the timeline panel.
- Deterministic tests for the timeline, the API routes, the CLI options, the CSV export, and the run page panel.

### Changed

- Version numbers moved to 1.9.0.
- The run detail page now shows when failures happened beside the trace waterfall.
- The trace waterfall rows carry span IDs so the timeline can link to them.
- The architecture now includes a run-level error timeline beside the trace inspection layer.

## 1.8.0 - 2026-08-04

### Added

- An agent comparison overlay on the dashboard failure trend.
- A second failure line that draws one agent beside the primary series.
- A compare select on the trend filter that lists the recorded agents except the primary one.
- A legend that shows the failure rate of each drawn series.
- `GET /api/trend/overlay` route that returns both trend series for scripts.
- `GET /api/trend/overlay?format=csv` route that returns the series as a CSV attachment.
- A `series` column in the overlay CSV that marks each row as primary or compare.
- `atw trend --compare <name>` command that prints both failure trend series.
- `atw trend --compare <name> --format csv` command that prints the series as CSV.
- Dashboard JSON and CSV download links that keep the compare agent and window.
- Deterministic tests for the overlay, the API routes, the CLI options, the CSV export, and the dashboard panel.

### Changed

- Version numbers moved to 1.8.0.
- The dashboard trend chart can now draw two failure lines on one time axis.
- The architecture now includes an agent comparison overlay beside the failure trend.
- CI now uses the latest GitHub Actions checkout and setup-python actions.

## 1.7.0 - 2026-08-04

### Added

- A status breakdown beside the daily failure line on the dashboard.
- A stacked bar per trend day that shows the run status counts.
- A status legend with window totals for each recorded run status.
- `GET /api/trend/statuses` route that returns the daily status counts.
- `GET /api/trend/statuses?format=csv` route that returns the counts as a CSV attachment.
- `atw trend --statuses` command that prints the daily status counts.
- `atw trend --statuses --format csv` command that prints the counts as CSV.
- The dashboard status panel keeps the active agent and window in its export links.
- Deterministic tests for the status trend, the API routes, the CLI options, the CSV export, and the dashboard panel.

### Changed

- Version numbers moved to 1.7.0.
- The dashboard trend section now shows the status composition of each day.
- The architecture now includes a status breakdown beside the failure trend.

## 1.6.0 - 2026-08-04

### Added

- A trend window selector on the dashboard chart for 7, 14, 30, and 90 day views.
- A per-day trend drill-down that lists the runs started on one UTC calendar day.
- Clickable day dots on the trend chart that open the drill-down for that day.
- A day panel on the dashboard that shows the day runs and links each one to its page.
- `GET /api/trend?days=<n>` option that sets the trend window for scripts.
- `GET /api/trend/{day}` route that returns the runs started on one day, with an `agent` filter.
- `GET /api/trend/{day}?format=csv` route that returns the day runs as a CSV attachment.
- `atw trend --day <YYYY-MM-DD>` command that lists the runs started on one day.
- `atw trend --day <YYYY-MM-DD> --format csv` command that prints the day runs as CSV.
- The dashboard export links keep the active window size and agent filter.
- Deterministic tests for the window selector, the drill-down, the day routes, the day CSV, and the CLI options.

### Changed

- Version numbers moved to 1.6.0.
- The dashboard trend filter now carries a window selector beside the agent filter.
- The dashboard keeps the selected day and window when it searches or changes filters.
- The architecture now includes a per-day trend drill-down beside the failure trend.

## 1.5.0 - 2026-08-04

### Added

- A CSV export for the daily failure trend through the CLI and the API.
- `atw trend` command that prints the failure buckets for a window.
- `atw trend --agent <name>` option that restricts the trend to one agent.
- `atw trend --format csv` option that prints the trend as a CSV document.
- `atw trend --agents` option that lists the agent names available for filtering.
- `GET /api/trend?agent=<name>` option that filters the trend buckets.
- `GET /api/trend?format=csv` route that returns the trend as a CSV attachment.
- `GET /api/trend/agents` route that lists the distinct agent names.
- An agent filter on the dashboard trend panel with JSON and CSV download links.
- A second sample agent trace that shows the filter on a multi-agent library.
- Deterministic tests for the agent filter, the CSV export, the CLI commands, and the API routes.

### Changed

- Version numbers moved to 1.5.0.
- The failure trend now accepts one agent name, so a reviewer can isolate one producer.
- The dashboard trend panel keeps the active agent in its export links.
- The architecture now includes a trend filter and trend CSV export.

## 1.4.0 - 2026-08-03

### Added

- A server-side retention scheduler that sweeps old evidence while the server runs.
- `ATW_CLEANUP_EVERY_SECONDS` environment variable that starts the background schedule.
- `ATW_CLEANUP_OLDER_THAN_DAYS` and `ATW_CLEANUP_KEEP_LABELED` options that set the schedule policy.
- `GET /api/cleanup/schedule` route that reports the active schedule and the last sweep.
- A scheduler status panel on the Cleanup page and a footer line on every page.
- A daily failure trend line on the dashboard with window totals.
- `GET /api/trend` route that returns the daily failure buckets for scripts.
- Deterministic tests for the scheduler, the schedule route, the trend, and the dashboard panel.

### Changed

- Version numbers moved to 1.4.0.
- The dashboard now draws a failure trend beside the recent run list.
- The architecture now includes a server-side scheduler beside the retention layer.
- The server lifecycle starts and stops the scheduler, so no background thread leaks.

## 1.3.0 - 2026-08-04

### Added

- A scheduled cleanup run through `atw cleanup`.
- `atw cleanup --every <seconds>` command that repeats a sweep on an interval.
- `atw cleanup --dry-run` command that previews a sweep without recording it.
- `atw cleanup --history` command that lists the recorded cleanup sweeps.
- A cleanup log table that records each scheduled sweep.
- `POST /api/cleanup` route that runs a sweep or previews one with `dry_run`.
- `GET /api/cleanup/history` route that lists the recorded sweeps.
- A Cleanup history section on the Cleanup page.
- A retention line in the library report that counts eligible and protected old runs.
- `atw report --older-than` option that changes the retention line policy.
- `GET /api/report?older_than_days=` option that changes the retention line policy.
- A retention section row in the report CSV export.
- Deterministic tests for the sweep, the history, the retention line, the CLI commands, and the API routes.

### Changed

- Version numbers moved to 1.3.0.
- The report JSON now carries a `retention` block.
- The report CSV now includes cutoff, eligible, protected, and last-cleanup columns.
- The architecture now includes a cleanup log beside the retention layer.

## 1.2.0 - 2026-08-03

### Added

- Per-run retention and cleanup of old evidence.
- `atw prune` command that deletes runs last ingested before a cutoff.
- `atw prune --dry-run` command that previews candidates without deleting.
- `POST /api/prune` route that prunes runs or previews them with `dry_run`.
- A Cleanup page that previews old runs and applies a retention policy.
- A label now protects a run from age-based cleanup. Set `--no-keep-labeled` to include labeled runs.
- `--run-id` option on `atw prune` to target specific runs.
- Deterministic tests for retention, pruning, the CLI command, and the API route.

### Changed

- Version numbers moved to 1.2.0.
- The lockfile moved from `requirements.lock` to `requirements-lock.txt`.
- The pip ecosystem can now evaluate the lockfile, so Dependabot no longer reports a dependency file error.
- CI installs from `requirements-lock.txt` and uses it as the pip cache key.
- The architecture now includes a retention layer beside the review list.

## 1.1.0 - 2026-08-03

### Added

- CSV export for the library report through the CLI and the API.
- `atw report --format csv` command that prints the report as one CSV document.
- `GET /api/report?format=csv` route that returns the report as a CSV attachment.
- A CSV download button on the library report page.
- Bulk label actions on the review list.
- `POST /api/review/labels` route that sets one label on several runs.
- `atw review --label <label>` command that labels every unreviewed run.
- `atw review --label <label> --run-id <id>` command that labels specific runs.
- Checkboxes and an apply-to-selected form on the review page.
- `requirements.lock` that pins the full resolved dependency tree.
- Dependency checks that verify the lockfile stays sorted and complete.

### Changed

- Version numbers moved to 1.1.0.
- The review list now triages several runs in one action.
- The library report downloads as CSV beside its JSON view.
- CI installs dependencies from the lockfile for reproducible builds.
- The architecture now includes bulk labeling and report CSV export.

## 1.0.0 - 2026-08-03

### Added

- A review list that shows runs without a review label.
- A library report that summarizes evidence by agent and source folder.
- `atw review` command that lists runs that still need a label.
- `atw report` command that prints the folder-level library summary.
- `GET /api/review` route that returns unlabeled runs.
- `GET /api/report` route that returns the library summary report.
- A Review page and a Report page in the interface.
- A source directory column on each run record, filled by the watcher and the ingest commands.
- Automatic migration that adds the source directory column to databases created before release 1.0.
- Deterministic tests for the review list, the report, the migration, the CLI commands, and the API routes.

### Changed

- Version numbers moved to 1.0.0.
- The navigation and the dashboard now link to the review list and the library report.
- Run summaries now include the local source directory.
- Dependabot updates requirements pins in place with a bump-versions strategy.

## 0.9.0 - 2026-08-03

### Added

- Local run labels and review notes stored beside each run.
- `atw annotate` command that sets or clears a label and a note.
- `PATCH /api/runs/{run_id}/annotations` route that updates both fields.
- A label badge and an annotations form on the run detail page.
- A label badge on dashboard run cards.
- Search matching on run labels.
- Automatic migration that adds the annotation columns to databases created before release 0.9.
- Deterministic tests for the storage methods, migration, search, the CLI command, and the API route.

### Changed

- Version numbers moved to 0.9.0.
- Run summaries now include the local label and note.
- Re-ingesting a trace keeps its label and note.
- The architecture now includes a local annotation layer beside the run record.

## 0.8.0 - 2026-08-03

### Added

- OTLP HTTP JSON span export for workbench operations to a local collector.
- `ATW_OTEL_COLLECTOR_ENDPOINT` environment variable that points the server at a local collector.
- On-demand collector export for recorded runs through `atw publish`.
- `POST /api/runs/{run_id}/export/collector` route that sends one run to a collector.
- A "Send to collector" action on the run detail page.
- A live collector endpoint line in the page footer.
- Deterministic tests for the collector export, the OTLP JSON span exporter, the CLI command, and the API route.

### Changed

- Version numbers moved to 0.8.0.
- Workbench operation spans export to the console or a local collector when configured.
- The architecture now includes a local collector export layer.

## 0.7.0 - 2026-08-03

### Added

- Field-level comparison details that list changed argument and result keys.
- Error change detection for each compared tool position.
- Comparison aggregates for added, removed, outcome, and error changes.
- A state filter on the compare page for same, changed, added, and removed rows.
- CSV export for comparison reports and run tool calls.
- `atw compare --format csv` and `atw export --format csv` on the CLI.
- `GET /api/compare?format=csv` and `GET /api/runs/{run_id}/export?format=csv` on the API.
- CSV download links on the compare page and the run page.
- Deterministic tests for CSV rendering, escaping, the CLI, and the API routes.

### Changed

- Version numbers moved to 0.7.0.
- The compare page now shows outcome changes and field-level deltas.
- The comparison report includes added, removed, outcome, and error counts.
- The architecture now includes a CSV export layer.

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

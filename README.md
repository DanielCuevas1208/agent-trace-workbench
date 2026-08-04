# Agent Trace Workbench

Agent Trace Workbench keeps agent-run evidence on your machine.

It records local JSON traces in SQLite. It replays tool calls with deterministic handlers. It compares runs by call order, timing, results, and outcomes.

Release 0.8 adds local OpenTelemetry collector export. Send workbench spans or a recorded run to a local collector over OTLP HTTP JSON. The data stays on your machine.

Release 0.9 adds run labels and review notes. Attach context to each run in the local database. The context survives re-ingestion and never leaves your machine.

Release 1.0 adds a review list and a folder-level library report. Triage runs without a label, then read the library totals by agent and source folder.

Release 1.1 adds a CSV export for the library report and bulk label actions on the review list. Download the report as CSV, or label several runs in one action.

Release 1.2 adds per-run retention and cleanup of old evidence. Prune runs last ingested before a cutoff. Preview the match first. A label protects a run from cleanup.

Release 1.3 adds a scheduled cleanup and a retention line to the library report. Run one sweep now, or loop one on an interval. The report shows how much old evidence remains.

Release 1.4 adds a server-side sweep scheduler and a failure trend line on the dashboard. The server can sweep old evidence on an interval while it runs. The dashboard shows whether failures rise or fall across the last 14 days.

Release 1.5 adds a CSV export for the failure trend and an agent-level trend filter on the dashboard. Filter the chart to one agent. Download the same series as CSV from the panel, the API, or the CLI.

Release 1.6 adds a trend window selector and a per-day drill-down on the dashboard. Choose 7, 14, 30, or 90 day views. Click a day to see the runs that started that day.

Release 1.7 adds a status breakdown beside the daily failure line. Each trend day shows a stacked bar of run status counts. Read the same counts from the API or the CLI.

Release 1.8 adds an agent comparison overlay to the failure trend. Choose a second agent on the dashboard and the chart draws its failure line beside the primary series. Read both series from the API, the CLI, or a CSV file.

## Value

Agent debugging needs evidence at tool boundaries.

This workbench makes each boundary visible. It shows inputs, outputs, timing, attributes, and errors in one local record.

The design supports repeatable review. A replay runs a registered local handler when available. It uses the recorded result when no handler exists.

No hosted service is required. The SQLite database stays in the local `data` directory. Multiple local tools can share the same database file.

The watcher supports a common handoff. An agent writes a JSON file. The workbench finds the file and records the run.

Import the OpenTelemetry JSON format to bring agent traces in. Export runs to portable JSON files to share or back them up.

## Architecture

```text
  local JSON trace         OTLP JSON file
        |                        |
        v                        v
 Pydantic contract <------> SQLite trace store ---> FastAPI views and JSON API
        ^                        |
        |                        +---------+---------+
 directory watcher               v                   v
                          replay engine       comparison engine
                              |                   |  ^
                              v                   v  |
                    handler config + guard    saved comparisons
                                                    |
                                                    v
                                              local export files
                                                    |
                                                    +---> OTLP HTTP JSON
                                                                  |
                                                                  v
                                            local OpenTelemetry collector
```

SQLite runs in WAL mode with a busy timeout. Readers keep a committed snapshot. Writers wait for the write lock.

- `models.py` defines the portable trace contract.
- `handlers.py` loads local handler config and applies side-effect guards.
- `storage.py` owns the SQLite schema, WAL coordination, idempotent ingestion, and local annotations. It also computes the review list, applies bulk labels, builds the library report, computes the daily failure trend, the status breakdown, and the agent comparison overlay, lists the runs for one day, and enforces the retention cutoff for cleanup. A cleanup log records each scheduled sweep.
- `ingestion.py` watches JSON files and returns stable schema error reports.
- `otlp.py` converts the OTLP JSON encoding to and from the trace contract.
- `replay.py` runs guarded local handlers and records mismatches.
- `compare.py` aligns tool calls by recorded position and reports field-level deltas.
- `export.py` renders comparisons, run tool calls, library reports, failure trends, and day run lists as CSV files.
- `collector.py` posts recorded runs to a local collector over OTLP HTTP JSON.
- `main.py` serves the interface and the JSON API.
- `scheduler.py` runs server-side retention sweeps on an interval.
- `telemetry.py` creates OpenTelemetry spans and exports them locally.

The OpenTelemetry integration stays local by default. Set `ATW_OTEL_CONSOLE=1` to print workbench spans. Set `ATW_OTEL_COLLECTOR_ENDPOINT` to export them to a local collector.

Each stored run keeps a local label and note. They form the review context for long-lived evidence. The server can sweep old evidence on an interval. The scheduler stays off unless you set `ATW_CLEANUP_EVERY_SECONDS`.

## Setup

Use Python 3.11 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`requirements.txt` pins the application and verification dependencies. `requirements-lock.txt` pins the full resolved tree for reproducible installs.

## Run the sample

Load the bundled traces.

```powershell
python -m agent_trace_workbench.cli ingest fixtures/run_baseline.json
python -m agent_trace_workbench.cli ingest fixtures/run_candidate.json
```

Start the local server.

```powershell
uvicorn agent_trace_workbench.main:app --reload
```

Open `http://127.0.0.1:8000` in a browser.

The dashboard shows both runs. The candidate includes a reservation failure. The trend panel draws the daily failure rate for the recorded evidence.

Load the second agent to exercise the trend filter.

```powershell
python -m agent_trace_workbench.cli ingest fixtures/run_support.json
```

The dashboard now lists two agents. Filter the trend panel to either one.

## Sample output

The baseline ingestion returns this summary shape:

```json
{
  "run_id": "run-baseline-001",
  "status": "ok",
  "duration_ms": 220.0,
  "tool_count": 2,
  "source_name": "run_baseline.json"
}
```

Replay the baseline from the command line.

```powershell
python -m agent_trace_workbench.cli replay run-baseline-001
```

The replay report contains two matching handler steps.

Compare the baseline with the candidate.

```powershell
python -m agent_trace_workbench.cli compare run-baseline-001 run-candidate-001
```

The comparison reports one changed result and one added failing call.

Watch a folder once.

```powershell
python -m agent_trace_workbench.cli --db data/workbench.db watch fixtures --once
```

The watcher scans top-level JSON files. It stores valid traces and returns file-level errors.

This example shows two valid files and one invalid file.

```json
{
  "discovered_files": 3,
  "processed_files": 3,
  "ingested_files": 2,
  "error_count": 1,
  "errors": [
    {
      "source_name": "broken.json",
      "kind": "invalid_json"
    }
  ]
}
```

Run the watcher continuously by omitting `--once`. It checks for changed files every two seconds.

## Search

Search the trace library by run ID, agent, span, or tool.

```powershell
python -m agent_trace_workbench.cli search catalog-assistant
```

The command prints matching run summaries.

```json
[
  {
    "run_id": "run-baseline-001",
    "agent_name": "catalog-assistant",
    "status": "ok",
    "tool_count": 2
  },
  {
    "run_id": "run-candidate-001",
    "agent_name": "catalog-assistant",
    "status": "error",
    "tool_count": 3
  }
]
```

Use the search box on the dashboard. The results replace the recent run list.

The JSON API accepts the same search.

```powershell
curl.exe "http://127.0.0.1:8000/api/runs?q=get_inventory"
```

Search matches partial text. It also matches a run label. It escapes `%`, `_`, and `!` in your query.

## Span filtering

Filter spans on a run page by kind, status, or tool.

The JSON API accepts the same filters.

```powershell
curl.exe "http://127.0.0.1:8000/api/runs/run-candidate-001?kind=tool&status=error"
```

The response keeps only matching spans. The run metrics still show full totals.

Use the drop-downs on a run page. Clear the filters to see every span.

## Failure trend

The dashboard draws a daily failure line for the last 14 days.

Each day counts total runs and failure runs. The line shows the failure rate. It helps you see whether failures rise or fall across a release.

Read the trend over the API.

```powershell
curl.exe "http://127.0.0.1:8000/api/trend?days=14"
```

The response lists one bucket per day. Empty days stay in the window with zero counts.

```json
[
  {
    "day": "2026-07-31",
    "runs": 2,
    "failures": 1,
    "failure_rate": 0.5
  }
]
```

The dashboard panel shows window totals for runs, failures, and the failure rate.

## Trend agent filter

Filter the trend to one agent from the dashboard.

Choose an agent in the drop-down above the chart. The panel redraws with that agent's runs only. Window totals match the filtered series.

The API accepts the same filter.

```powershell
curl.exe "http://127.0.0.1:8000/api/trend?agent=catalog-assistant"
```

The response keeps one bucket per day for that agent.

```json
[
  {
    "day": "2026-07-31",
    "runs": 2,
    "failures": 1,
    "failure_rate": 0.5
  }
]
```

List the agents that have recorded runs.

```powershell
curl.exe "http://127.0.0.1:8000/api/trend/agents"
```

Use the CLI for scripts.

```powershell
python -m agent_trace_workbench.cli trend --agent catalog-assistant --days 14
```

The `--agents` flag prints the same list.

```powershell
python -m agent_trace_workbench.cli trend --agents
```

## Trend CSV export

Download the trend as a CSV document.

```powershell
curl.exe -o failure-trend.csv "http://127.0.0.1:8000/api/trend?format=csv"
```

The file lists one row per day. The agent column repeats the active filter. It stays empty for the all-agents view.

```text
day,agent_name,runs,failures,failure_rate
2026-07-31,,2,1,0.5
```

Print the same file from the CLI.

```powershell
python -m agent_trace_workbench.cli trend --format csv
```

Keep the agent filter in the export.

```powershell
curl.exe -o catalog.csv "http://127.0.0.1:8000/api/trend?agent=catalog-assistant&format=csv"
python -m agent_trace_workbench.cli trend --agent catalog-assistant --format csv
```

The dashboard panel links to both downloads. The links keep the active agent.

## Trend window

Choose the trend window on the dashboard chart.

Select 7, 14, 30, or 90 days beside the agent filter. The chart redraws with that window. Window totals match the selected span.

```powershell
curl.exe "http://127.0.0.1:8000/api/trend?days=30"
```

The response keeps one bucket per day across the full window. Empty days stay in the series.

The JSON and CSV export links keep the active window. The default is 14 days.

```powershell
curl.exe "http://127.0.0.1:8000/api/trend?days=30&format=csv"
python -m agent_trace_workbench.cli trend --days 30
```

## Day drill-down

Click a day on the trend chart. The dashboard opens a panel with the runs that started that day.

Each day dot links to that panel. The panel lists one run per card. Each card links to its run page.

```powershell
curl.exe "http://127.0.0.1:8000/api/trend/2026-07-31"
```

The response lists the runs for that day.

```json
{
  "day": "2026-07-31",
  "agent": "",
  "runs": [
    {
      "run_id": "run-baseline-001",
      "agent_name": "catalog-assistant",
      "status": "ok",
      "tool_count": 2
    },
    {
      "run_id": "run-candidate-001",
      "agent_name": "catalog-assistant",
      "status": "error",
      "tool_count": 3
    }
  ]
}
```

Filter the day view to one agent.

```powershell
curl.exe "http://127.0.0.1:8000/api/trend/2026-07-31?agent=catalog-assistant"
```

The day panel on the dashboard keeps the active agent. Change the agent or the window on the trend form. The day view follows both.

Download the day runs as CSV.

```powershell
curl.exe -o runs-2026-07-31.csv "http://127.0.0.1:8000/api/trend/2026-07-31?format=csv"
```

The file lists one row per run. The day cell repeats the drill target.

```text
day,run_id,agent_name,status,tool_count,duration_ms,source_dir,label
2026-07-31,run-baseline-001,catalog-assistant,ok,2,220.0,fixtures,
```

Use the CLI for scripts.

```powershell
python -m agent_trace_workbench.cli trend --day 2026-07-31
python -m agent_trace_workbench.cli trend --day 2026-07-31 --format csv
```

The day panel offers the same CSV download. A day outside the active window is ignored. The dashboard draws no panel for it.

## Status breakdown

The dashboard shows which run statuses shape each trend day.

Each day draws one stacked bar beside the failure line. The bar shows how many runs ended ok and how many failed. A legend lists the window totals for each status.

Read the breakdown over the API.

```powershell
curl.exe "http://127.0.0.1:8000/api/trend/statuses?days=14"
```

The response lists one bucket per day. Each bucket maps status names to run counts.

```json
[
  {
    "day": "2026-07-31",
    "runs": 2,
    "statuses": {
      "ok": 1,
      "error": 1
    }
  }
]
```

Filter the breakdown to one agent.

```powershell
curl.exe "http://127.0.0.1:8000/api/trend/statuses?agent=catalog-assistant"
```

The dashboard panel keeps the active agent and window. It follows the same trend filter.

Download the breakdown as CSV.

```powershell
curl.exe -o status-trend.csv "http://127.0.0.1:8000/api/trend/statuses?format=csv"
```

The file lists one row per status on a day. Empty days produce no rows.

```text
day,agent_name,status,runs
2026-07-31,,ok,1
2026-07-31,,error,1
```

Use the CLI for scripts.

```powershell
python -m agent_trace_workbench.cli trend --statuses
python -m agent_trace_workbench.cli trend --statuses --agent catalog-assistant --format csv
```

The panel JSON and CSV links keep the active agent and window.

## Agent comparison overlay

Compare one agent's failure line with another on the dashboard.

Choose a second agent in the compare select above the chart. The chart draws a dashed line for that agent beside the primary line. The legend shows the failure rate of each series.

The compare select lists every recorded agent except the primary one. It appears only when the library records two or more agents.

Read both series over the API.

```powershell
curl.exe "http://127.0.0.1:8000/api/trend/overlay?agent=catalog-assistant&compare=support-assistant"
```

The response returns the primary series and the compare series on one window.

```json
{
  "days": 14,
  "primary_agent": "catalog-assistant",
  "compare_agent": "support-assistant",
  "primary": [
    {
      "day": "2026-07-31",
      "runs": 2,
      "failures": 1,
      "failure_rate": 0.5
    }
  ],
  "compare": [
    {
      "day": "2026-07-31",
      "runs": 1,
      "failures": 0,
      "failure_rate": 0.0
    }
  ]
}
```

Omit `agent` to compare the whole library with one agent.

```powershell
curl.exe "http://127.0.0.1:8000/api/trend/overlay?compare=support-assistant"
```

Download the overlay as a CSV document.

```powershell
curl.exe -o overlay.csv "http://127.0.0.1:8000/api/trend/overlay?compare=support-assistant&format=csv"
```

The file lists one row per day per series. A `series` column marks each row as primary or compare.

```text
day,series,agent_name,runs,failures,failure_rate
2026-07-31,primary,catalog-assistant,2,1,0.5
2026-07-31,compare,support-assistant,1,0,0.0
```

Use the CLI for scripts.

```powershell
python -m agent_trace_workbench.cli trend --agent catalog-assistant --compare support-assistant
python -m agent_trace_workbench.cli trend --compare support-assistant --format csv
```

The dashboard panel links to both downloads. The links keep the active agents and window. The compare agent must differ from the primary agent.

## Saved comparisons

Save a comparison for later review.

```powershell
python -m agent_trace_workbench.cli comparisons
```

List saved comparisons with the CLI.

```powershell
python -m agent_trace_workbench.cli comparisons --limit 10
```

Delete a saved comparison.

```powershell
python -m agent_trace_workbench.cli comparisons --delete <comparison-id>
```

The compare page saves a comparison with a label. Use the JSON API for scripts.

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/comparisons `
  -H "content-type: application/json" `
  -d "{\"run_a\": \"run-baseline-001\", \"run_b\": \"run-candidate-001\", \"label\": \"v1 vs v2\"}"
```

The saved record keeps the comparison report.

```json
{
  "comparison_id": "4f9c2a1e8b6d",
  "label": "v1 vs v2",
  "run_a": "run-baseline-001",
  "run_b": "run-candidate-001",
  "report": {
    "changed_tools": 2,
    "total_duration_delta_ms": 60.0
  }
}
```

List, get, and delete use these API routes.

- `GET /api/comparisons` lists saved comparisons.
- `GET /api/comparisons/{id}` returns one saved comparison.
- `DELETE /api/comparisons/{id}` removes one saved comparison.

## Comparison detail

The compare page shows counts for changed, added, removed, outcome, and error changes.

Each row marks argument, result, and outcome deltas. Rows with field-level changes open to show the changed argument and result keys.

The JSON report carries the same detail. This excerpt shows the new aggregate fields.

```json
{
  "changed_tools": 2,
  "added_tools": 1,
  "removed_tools": 0,
  "outcome_changed_tools": 1,
  "error_changed_tools": 1,
  "tool_diffs": [
    {
      "index": 2,
      "state": "changed",
      "result_changed": true,
      "result_keys_changed": ["available"]
    }
  ]
}
```

Filter the table by state with the buttons above the list. Use them to focus on added or changed calls.

## CSV export

Export a comparison to a CSV file.

```powershell
python -m agent_trace_workbench.cli compare run-baseline-001 run-candidate-001 --format csv
```

The command prints one row per tool position.

```text
index,state,tool_a,tool_b,arguments_changed,outcome_changed,result_changed,error_changed,duration_delta_ms,error_a,error_b,argument_keys_changed,result_keys_changed
1,same,search_catalog,search_catalog,no,no,no,no,15.0,,,,
2,changed,get_inventory,get_inventory,no,no,yes,no,15.0,,,,available
3,added,,reserve_inventory,no,yes,yes,yes,,,reservation window expired,,
```

Export a run's tool calls with the export command.

```powershell
python -m agent_trace_workbench.cli export run-baseline-001 --format csv
```

The command writes `data/exports/run-baseline-001.csv`. Each row is one tool call with its arguments and result as JSON cells.

The API returns both files.

```powershell
curl.exe "http://127.0.0.1:8000/api/compare?run_a=run-baseline-001&run_b=run-candidate-001&format=csv"
curl.exe -o run.csv "http://127.0.0.1:8000/api/runs/run-baseline-001/export?format=csv"
```

The compare page and the run page offer the same downloads.

## OTLP import

Import the OpenTelemetry JSON encoding. The format matches an `ExportTraceServiceRequest` file.

```powershell
python -m agent_trace_workbench.cli import-otlp traces.otlp.json
```

The command converts each resource span group into one run and stores it.

```json
{
  "source": "traces.otlp.json",
  "imported_runs": 2,
  "runs": [
    {
      "run_id": "catalog-assistant-5b8efff79803",
      "status": "ok",
      "tool_count": 2
    },
    {
      "run_id": "catalog-assistant-aa7c91dfb18a",
      "status": "error",
      "tool_count": 3
    }
  ]
}
```

The mapping rules are stable.

- `service.name` becomes `agent_name`.
- `service.version` becomes `agent_version`.
- The first span `traceId` becomes `trace_id`.
- `run_id` derives from the service slug and the first 12 trace ID characters.
- Span status maps from the OTLP status code.
- Typed attribute values convert to plain JSON values.
- Spans become internal spans unless they carry workbench kind data.

The JSON API accepts the same payload.

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/otlp/traces `
  -H "content-type: application/json" `
  --data-binary "@traces.otlp.json"
```

The response lists each stored run. The workbench reads the OTLP JSON encoding only. It does not read protobuf binary files.

## Export files

Export a run to a portable JSON file.

```powershell
python -m agent_trace_workbench.cli export run-baseline-001
```

The command writes `data/exports/run-baseline-001.json`. The file follows the trace contract and re-ingests on any workbench.

```json
{
  "exported": [
    {
      "run_id": "run-baseline-001",
      "format": "json",
      "path": "data/exports/run-baseline-001.json"
    }
  ]
}
```

Export every run by omitting the run ID.

```powershell
python -m agent_trace_workbench.cli export --output data/backups
```

Choose a directory with `--output`. For one run, `--output` may name a file. Choose a format with `--format otlp`.

```powershell
python -m agent_trace_workbench.cli export run-candidate-001 --format otlp
```

The OTLP file imports back through `import-otlp`. The round trip keeps arguments, results, outcomes, and errors.

Export one run from the API.

```powershell
curl.exe -o run.json http://127.0.0.1:8000/api/runs/run-baseline-001/export
curl.exe -o run.otlp.json http://127.0.0.1:8000/api/runs/run-baseline-001/export?format=otlp
```

The run page offers the same downloads.

## Collector export

Send a recorded run to a local OpenTelemetry collector. Use Jaeger, Tempo, or the OpenTelemetry Collector. The run appears with its spans in that tool.

Point the server at a local collector.

```powershell
$env:ATW_OTEL_COLLECTOR_ENDPOINT = "http://127.0.0.1:4318"
uvicorn agent_trace_workbench.main:app
```

Workbench operation spans export to that endpoint. The page footer shows the active endpoint.

Publish a run from the command line.

```powershell
python -m agent_trace_workbench.cli publish run-baseline-001 --endpoint http://127.0.0.1:4318
```

The command posts the OTLP JSON encoding to `{endpoint}/v1/traces`.

```json
{
  "endpoint": "http://127.0.0.1:4318",
  "exported_runs": [
    {
      "run_id": "run-baseline-001",
      "status": "accepted",
      "span_count": 4,
      "endpoint": "http://127.0.0.1:4318",
      "detail": null
    }
  ]
}
```

Publish every run by omitting the run ID.

```powershell
python -m agent_trace_workbench.cli publish --endpoint http://127.0.0.1:4318
```

Send one run from the API.

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/runs/run-baseline-001/export/collector `
  -H "content-type: application/json" `
  -d "{\"endpoint\": \"http://127.0.0.1:4318\"}"
```

The run page offers the same action. The button posts the run and shows the outcome.

A failed export keeps the report. It reports the HTTP status or the connection error. The workbench retries a request up to three times. The endpoint may omit the scheme. The workbench adds `http://` when needed.

## Shared database

One SQLite file can serve a watcher, a server, and a CLI command at once.

The store runs in WAL mode. A reader sees the last committed snapshot. It never waits for a writer.

Each connection sets a busy timeout. A writer waits for the write lock instead of failing on first contact.

Write operations retry when the database reports a lock. This matches the watcher's steady write pattern.

Show the active settings with the CLI.

```powershell
python -m agent_trace_workbench.cli store
```

The command prints the journal mode, busy timeout, and SQLite version.

```json
{
  "db_path": "data/workbench.db",
  "journal_mode": "wal",
  "busy_timeout_ms": 5000,
  "synchronous": "normal",
  "sqlite_version": "3.45.1"
}
```

The JSON API exposes the same settings.

```powershell
curl.exe http://127.0.0.1:8000/api/store
```

Change the busy timeout for the server with `ATW_DB_BUSY_TIMEOUT_MS`.

```powershell
$env:ATW_DB_BUSY_TIMEOUT_MS = "2000"
uvicorn agent_trace_workbench.main:app
```

The footer on every page shows the live store settings.

## Replay handlers

Replay is deterministic. It runs a local handler when one exists for a tool. It uses the recorded result when no handler exists.

A JSON file maps each tool to a behavior. The file can hold a script path or a fixed result.

The bundled config maps the sample tools.

```json
{
  "policy": "strict",
  "handlers": [
    {
      "tool": "search_catalog",
      "script": "handlers/search_catalog.py",
      "side_effect": "read_only"
    },
    {
      "tool": "get_inventory",
      "script": "handlers/get_inventory.py",
      "side_effect": "read_only"
    },
    {
      "tool": "reserve_inventory",
      "result": {
        "reservation_id": "rsv-9ab1",
        "status": "confirmed"
      },
      "side_effect": "local_write"
    }
  ]
}
```

Each handler needs one behavior. Use `script` for a Python file with a `run(arguments)` function. Use `result` for a fixed JSON value.

Script paths are relative to the config file. The server loads `ATW_HANDLERS_CONFIG` when set.

## Side-effect guard

Each handler declares a side-effect level. The level can be `read_only`, `local_write`, `network`, or `unknown`.

A replay policy sets the allowed budget.

- `strict` runs read_only handlers only.
- `local` runs read_only and local_write handlers.
- `all` runs every handler.

The guard blocks a handler above the policy budget. It uses the recorded result for that step instead. The report marks the step as `guarded`.

Replay with the bundled config.

```powershell
python -m agent_trace_workbench.cli replay run-candidate-001 --config fixtures/handlers.json
```

The strict policy guards the reservation handler. It preserves the recorded failure.

The report shows the guarded step.

```json
{
  "run_id": "run-candidate-001",
  "policy": "strict",
  "total_steps": 3,
  "matched_steps": 2,
  "failed_steps": 1,
  "guarded_steps": 1,
  "steps": [
    {
      "index": 3,
      "tool_name": "reserve_inventory",
      "mode": "guarded",
      "recorded_outcome": "failure",
      "replayed_outcome": "failure",
      "result_match": true,
      "guarded": true,
      "side_effect_level": "local_write",
      "policy": "strict"
    }
  ]
}
```

Raise the budget for one run.

```powershell
python -m agent_trace_workbench.cli replay run-candidate-001 --config fixtures/handlers.json --policy local
```

The reservation handler now runs. It returns the fixed confirmation. The guard clears, and the result no longer matches.

## Run labels and notes

Attach a label and notes to a run for later review. The data stays beside the run in the local database.

```powershell
python -m agent_trace_workbench.cli annotate run-baseline-001 --label golden --note "reference run for the v2 regression"
```

The command prints the stored values.

```json
{
  "run_id": "run-baseline-001",
  "label": "golden",
  "note": "reference run for the v2 regression"
}
```

Clear both fields with `--clear`.

```powershell
python -m agent_trace_workbench.cli annotate run-baseline-001 --clear
```

The run page shows a label badge and an editable notes box. The dashboard shows the label on each run card.

Use the JSON API for scripts.

```powershell
curl.exe -X PATCH http://127.0.0.1:8000/api/runs/run-baseline-001/annotations `
  -H "content-type: application/json" `
  -d "{\"label\": \"golden\", \"note\": \"reference run\"}"
```

A label is at most 80 characters. A note is at most 2000 characters. An empty value clears one field. Search matches labels, so `atw search golden` finds the run.

Re-ingesting a trace keeps its label and note. The annotation stays local and never enters the portable trace contract.

## Review list

Find runs that still need a review label.

```powershell
python -m agent_trace_workbench.cli review
```

The command lists every unlabeled run.

```json
[
  {
    "run_id": "run-candidate-001",
    "agent_name": "catalog-assistant",
    "status": "error",
    "tool_count": 3
  }
]
```

Label a run on its page. It leaves the review list.

The JSON API accepts the same query.

```powershell
curl.exe "http://127.0.0.1:8000/api/review"
```

The review page links each run to its annotation form.

## Bulk labeling

Apply one label to several runs at once.

```powershell
python -m agent_trace_workbench.cli review --label triaged
```

The command labels every unreviewed run.

```json
{
  "label": "triaged",
  "updated": 2
}
```

Label specific runs by repeating `--run-id`.

```powershell
python -m agent_trace_workbench.cli review --label golden --run-id run-baseline-001
```

The review page offers the same action. Check the runs you reviewed, type a label, and apply it. The labeled runs leave the list.

The JSON API accepts a batch.

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/review/labels `
  -H "content-type: application/json" `
  -d "{\"run_ids\": [\"run-baseline-001\", \"run-candidate-001\"], \"label\": \"triaged\"}"
```

The response shows the count of updated runs. An empty label clears the review flag on each listed run. One batch labels at most 100 runs.

## Library report

Summarize the local trace library by agent and source folder.

```powershell
python -m agent_trace_workbench.cli report
```

The report shows library totals and one row per source folder.

```json
{
  "totals": {
    "runs": 2,
    "ok_runs": 1,
    "failure_runs": 1,
    "labeled_runs": 0,
    "unlabeled_runs": 2,
    "tool_calls": 5,
    "agents": 1,
    "sources": 1,
    "total_duration_ms": 500.0
  },
  "by_source": [
    {
      "source_dir": "fixtures",
      "runs": 2,
      "failure_runs": 1,
      "unlabeled_runs": 2,
      "tool_calls": 5,
      "agents": 1
    }
  ]
}
```

A source folder is the directory that produced the run. The watcher records it. The ingest commands record their file parent. API-ingested runs group under `api`.

The report page shows the same numbers as tables.

```powershell
curl.exe "http://127.0.0.1:8000/api/report"
```

The JSON API returns the same document.

The report carries a retention line. It shows how many runs a prune would remove, how many labels protect old runs, and when the last cleanup ran.

```json
"retention": {
  "older_than_days": 30,
  "cutoff": "2026-07-04T12:00:00+00:00",
  "eligible_runs": 1,
  "protected_runs": 1,
  "last_cleanup_at": "2026-08-04 02:05:58"
}
```

Test another policy with `--older-than` or the `older_than_days` query.

```powershell
python -m agent_trace_workbench.cli report --older-than 14
curl.exe "http://127.0.0.1:8000/api/report?older_than_days=14"
```

The report page shows the retention line at the bottom.

## Report CSV

Download the library report as one CSV document.

```powershell
python -m agent_trace_workbench.cli report --format csv
```

The document keeps every section in one file. A `section` column marks each row as the library total, one source folder, one agent, or the retention line.

```text
section,source_dir,agent_name,runs,ok_runs,failure_runs,labeled_runs,unlabeled_runs,tool_calls,agents,avg_duration_ms,total_duration_ms,cutoff,eligible_runs,protected_runs,last_cleanup_at
total,,,2,1,1,0,2,5,1,,500.0,,,,
source,fixtures,,2,,1,,2,5,1,,,,
agent,,catalog-assistant,2,,1,,2,5,,250.0,,,,
retention,,,,,,,,,,,,"2026-07-04T12:00:00+00:00",1,1,
```

Empty cells mean the section does not carry that metric. The retention row reports the old-evidence line. The report page and the API offer the same download.

```powershell
curl.exe -o library-report.csv "http://127.0.0.1:8000/api/report?format=csv"
```

The API returns the file as an attachment. The CSV keeps commas and quotes escaped, so it opens cleanly in any spreadsheet tool.

## Retention and cleanup

Delete runs last ingested before a cutoff. This keeps the local library focused on recent evidence.

The cutoff counts from the last ingestion time. A re-ingest resets the clock.

Preview the match first.

```powershell
python -m agent_trace_workbench.cli prune --older-than 30 --dry-run
```

The command prints the candidate runs without deleting them.

```json
{
  "older_than_days": 30,
  "cutoff": "2026-07-04T12:00:00+00:00",
  "keep_labeled": true,
  "dry_run": true,
  "protected_runs": 1,
  "deleted_runs": 0,
  "deleted_spans": 0,
  "deleted_comparisons": 0,
  "run_ids": [
    "run-candidate-001"
  ]
}
```

A label protects a run. The default policy keeps labeled runs, because a label marks evidence worth keeping. The report shows how many runs the labels protect.

Apply the cleanup by omitting `--dry-run`.

```powershell
python -m agent_trace_workbench.cli prune --older-than 30
```

The command deletes each matching run. The delete removes the run spans too. It also removes any saved comparison that references the run.

```json
{
  "older_than_days": 30,
  "cutoff": "2026-07-04T12:00:00+00:00",
  "keep_labeled": true,
  "dry_run": false,
  "protected_runs": 1,
  "deleted_runs": 1,
  "deleted_spans": 4,
  "deleted_comparisons": 0,
  "run_ids": [
    "run-candidate-001"
  ]
}
```

Target specific runs with `--run-id`.

```powershell
python -m agent_trace_workbench.cli prune --older-than 30 --run-id run-candidate-001
```

Include labeled runs in the cleanup with `--no-keep-labeled`.

The JSON API accepts the same policy.

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/prune `
  -H "content-type: application/json" `
  -d "{\"older_than_days\": 30, \"dry_run\": true}"
```

The response lists the candidate runs. Set `dry_run` to false to delete them.

The Cleanup page shows the same preview. It lists the matching runs and their last ingestion times. The page asks for confirmation before it deletes.

`older_than_days` must be at least one. A label is the only protection a run has. An explicit `--run-id` still obeys that protection.

## Scheduled cleanup

Run the retention policy on a schedule. The cleanup command runs one sweep now, or repeats one on an interval.

```powershell
python -m agent_trace_workbench.cli cleanup
```

The command applies the default 30-day policy. It records each sweep in the local cleanup log.

```json
{
  "older_than_days": 30,
  "cutoff": "2026-07-04T12:00:00+00:00",
  "keep_labeled": true,
  "dry_run": false,
  "protected_runs": 1,
  "deleted_runs": 1,
  "deleted_spans": 4,
  "deleted_comparisons": 0,
  "run_ids": ["run-candidate-001"],
  "sweep_id": "33f26fa21786",
  "ran_at": "2026-08-04 02:05:58"
}
```

Preview a sweep with `--dry-run`. A dry run never records history.

```powershell
python -m agent_trace_workbench.cli cleanup --older-than 30 --dry-run
```

Schedule a sweep with `--every`.

```powershell
python -m agent_trace_workbench.cli cleanup --older-than 30 --every 3600
```

The command sweeps once, waits the interval, and sweeps again. Stop it with Ctrl+C. Use cron or Task Scheduler for a fixed schedule.

List the recorded sweeps.

```powershell
python -m agent_trace_workbench.cli cleanup --history
```

The JSON API runs the same sweep.

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/cleanup `
  -H "content-type: application/json" `
  -d "{\"older_than_days\": 30}"
```

List the history over the API.

```powershell
curl.exe "http://127.0.0.1:8000/api/cleanup/history"
```

The Cleanup page shows the recent sweeps below the preview table.

## Server-side sweep scheduler

The server can sweep old evidence on an interval while it runs.

Set `ATW_CLEANUP_EVERY_SECONDS` before you start the server.

```powershell
$env:ATW_CLEANUP_EVERY_SECONDS = "3600"
uvicorn agent_trace_workbench.main:app
```

The server starts a background sweep on that interval. Each pass follows the same policy as `atw cleanup`. It records the sweep in the cleanup log.

Set the policy with two more variables.

```powershell
$env:ATW_CLEANUP_OLDER_THAN_DAYS = "30"
$env:ATW_CLEANUP_KEEP_LABELED = "1"
```

The scheduler stays off by default. No background delete starts without the interval variable.

Check the schedule over the API.

```powershell
curl.exe "http://127.0.0.1:8000/api/cleanup/schedule"
```

The response shows whether the scheduler runs and when the last sweep happened.

```json
{
  "enabled": true,
  "interval_seconds": 3600.0,
  "older_than_days": 30,
  "keep_labeled": true,
  "last_sweep_at": "2026-08-03 12:00:00.123456"
}
```

The Cleanup page shows the same state. The footer on every page shows the active interval.

## Trace contract

A trace requires `trace_id`, `run_id`, `agent_name`, and `spans`.

Each span requires an ID, name, kind, timestamps, and status.

A tool span also requires `tool_call` data.

`tool_call` stores the name, arguments, result, outcome, and optional error.

See `fixtures/run_baseline.json` for a complete example.

Ingest any compatible document with the API.

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/traces `
  -H "content-type: application/json" `
  --data-binary "@fixtures/run_baseline.json"
```

## Test status

The test suite covers the core flows. It covers storage, ingestion, replay, comparison, search, annotations, bulk labels, export, review, reports, retention cleanup, and scheduled cleanup. It covers the CLI, the API, collector export, the server scheduler, and the dashboard failure trend, including the agent filter, the window selector, the day drill-down, the status breakdown, the agent comparison overlay, and the CSV exports.

Run the checks with these commands.

```powershell
python -m pytest
ruff check .
python scripts/check_requirements.py
python -m compileall agent_trace_workbench tests
```

Current verification passes 329 tests, Ruff lint, dependency checks, and Python compilation. CI installs from `requirements-lock.txt` and runs these checks on Python 3.11, 3.12, and 3.13 for every push and pull request.

## Limitations

Replay does not call external tools. Unknown tools use their recorded results.

The guard trusts the declared side-effect level. It does not inspect the handler code.

Comparison aligns tool calls by recorded position. It does not infer semantic call identity.

Search uses SQL `LIKE` matching. It does not rank results by relevance.

Labels and notes stay local to the workbench database. Portable export files do not carry them.

The review list shows runs with an empty label. A blank label counts as unreviewed.

Bulk labeling sets the label only. It leaves the notes on each run untouched.

Retention counts from the last ingestion time. A re-ingest resets the clock.

A label protects a run from age-based cleanup. Remove the label to make the run eligible again.

A prune deletes the run, its spans, and any saved comparison that references it. Export important runs before a prune.

The cleanup page deletes every run in the preview table. It does not support per-row selection.

A scheduled cleanup runs only while the cleanup command runs. Stop the process to pause the schedule.

The server scheduler runs only while the server process stays open. Stop the server to pause the schedule.

A dry-run sweep never records history. Use the real sweep to keep the log current.

The failure trend groups by the calendar day a run started. It uses the UTC day.

The trend counts a run by its recorded status. A run with any error span counts as a failure.

The status breakdown groups by the same UTC calendar day as the trend.

The status breakdown counts runs by their recorded status. A run with an error span counts as an error run.

The status breakdown draws one stacked bar per day. The bar height scales to the busiest day in the window.

The status CSV lists one row per status present on a day. Empty days produce no rows.

The trend agent filter matches the exact recorded agent name.

The trend CSV repeats the active agent in every row. The all-agents view leaves that cell empty.

The trend export lists one row per day. It does not add a window total row.

The compare overlay matches each agent name exactly. An unknown name draws a flat line at zero.

The compare overlay shares the primary trend window. It does not add a third series.

The overlay CSV repeats the agent name in every row. The all-agents view leaves the primary cell empty.

The overlay CSV lists both series in one file. Plot tools filter rows by the series column.

The day drill-down stays bound to the primary series. It does not drill into the compare line.

The day drill-down groups runs by the UTC calendar day they started. It ignores a day outside the active trend window.

The day CSV repeats the active agent in every row. The all-agents view leaves that cell empty.

The cleanup history records policy and counts. It does not store the deleted traces.

The report retention line counts runs under the current policy. It uses `older_than_days` from the request or the 30-day default.

The report CSV keeps every section in one file. Spreadsheet users filter rows by the section column.

The library report groups by the recorded source folder. API-ingested runs group under `api`. A re-ingest updates the source folder to the latest ingestion.

OTLP import reads the JSON encoding only. It does not read protobuf binary files.

OTLP spans become internal spans unless they carry workbench kind attributes.

The exporter stores workbench fields as `workbench.*` attributes.

`run_id` derives from the service name and trace ID. It may differ from the producer's run label.

SQLite allows one writer at a time. A writer waits for the busy timeout, then the write retries and reports the error.

WAL mode creates `-wal` and `-shm` files beside the database.

The UI accepts one trace document per request. Use the CLI watcher for directory ingestion.

The watcher scans one directory level. It does not recurse into child directories.

The watcher uses file size and modification time. A rare same-size, same-time rewrite may wait for the next change.

OpenTelemetry spans cover workbench operations. Recorded agent runs export on demand only. The release does not push agent data to a remote collector automatically.

CSV exports keep arguments and results as JSON cells. Spreadsheet tools cannot index inside those cells.

Workbench spans export only when you set `ATW_OTEL_CONSOLE` or `ATW_OTEL_COLLECTOR_ENDPOINT`.

Collector export uses the OTLP HTTP JSON encoding. It does not use gRPC.

The collector export sends over plain HTTP. It does not use TLS or authentication.

The span exporter sends each workbench span as it ends. It does not batch spans.

## Roadmap

- Release 0.4 complete: add search, span filtering, and saved comparisons.
- Release 0.5 complete: add OTLP import and local export files.
- Release 0.6 complete: add coordination for shared databases.
- Release 0.7 complete: add richer comparison views and CSV export.
- Release 0.8 complete: export workbench spans to a local OpenTelemetry collector.
- Release 0.9 complete: add run labels and notes for long-lived evidence review.
- Release 1.0 complete: add a review list for runs without labels and a folder-level summary report.
- Release 1.1 complete: add a CSV export for the library report and bulk label actions on the review list.
- Release 1.2 complete: add per-run retention and cleanup of old evidence.
- Release 1.3 complete: add a scheduled cleanup run and a retention line to the library report.
- Release 1.4 complete: add a server-side sweep scheduler and a failure trend line on the dashboard.
- Release 1.5 complete: add a CSV export for the failure trend and an agent-level trend filter on the dashboard.
- Release 1.6 complete: add a trend window selector and a per-day drill-down on the dashboard chart.
- Release 1.7 complete: add a status breakdown beside the daily failure line on the dashboard.
- Release 1.8 complete: add an agent comparison overlay to the failure trend.
- Release 1.9: add a run-level error timeline to the run detail page.

## Repository map

`fixtures/` contains meaningful baseline, candidate, and second-agent traces. It also contains a handler config and demo scripts.

`tests/` contains deterministic tests for the core. It covers coordination, guards, search, annotations, OTLP, export, review, reports, retention cleanup, scheduled cleanup, the server scheduler, and the failure trend, including the agent filter, the window selector, the day drill-down, the status breakdown, the agent comparison overlay, and the CSV exports.

`static/` and `templates/` contain the presentation layer.

`scripts/` contains the dependency pin check used by CI.

`data/` is created at runtime and remains ignored by Git.

`requirements.txt` pins the direct dependencies used by local setup and CI. `requirements-lock.txt` pins the full resolved tree for reproducible installs.

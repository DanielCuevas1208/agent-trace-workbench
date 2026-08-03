# Agent Trace Workbench

Agent Trace Workbench keeps agent-run evidence on your machine.

It records local JSON traces in SQLite. It replays tool calls with deterministic handlers. It compares runs by call order, timing, results, and outcomes.

Release 0.7 adds richer comparison views and CSV export. Field-level deltas show which keys changed. CSV files carry comparisons and tool calls into any spreadsheet tool.

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
```

SQLite runs in WAL mode with a busy timeout. Readers keep a committed snapshot. Writers wait for the write lock.

- `models.py` defines the portable trace contract.
- `handlers.py` loads local handler config and applies side-effect guards.
- `storage.py` owns the SQLite schema, WAL coordination, and idempotent ingestion.
- `ingestion.py` watches JSON files and returns stable schema error reports.
- `otlp.py` converts the OTLP JSON encoding to and from the trace contract.
- `replay.py` runs guarded local handlers and records mismatches.
- `compare.py` aligns tool calls by recorded position and reports field-level deltas.
- `export.py` renders comparisons and run tool calls as CSV files.
- `main.py` serves the interface and the JSON API.
- `telemetry.py` creates OpenTelemetry spans for local operations.

The OpenTelemetry integration stays local by default. Set `ATW_OTEL_CONSOLE=1` to print workbench spans.

## Setup

Use Python 3.11 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`requirements.lock` pins application and verification dependencies.

## Run the sample

Load both bundled traces.

```powershell
python -m agent_trace_workbench.cli ingest fixtures/run_baseline.json
python -m agent_trace_workbench.cli ingest fixtures/run_candidate.json
```

Start the local server.

```powershell
uvicorn agent_trace_workbench.main:app --reload
```

Open `http://127.0.0.1:8000` in a browser.

The dashboard shows both runs. The candidate includes a reservation failure.

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

Search matches partial text. It escapes `%`, `_`, and `!` in your query.

## Span filtering

Filter spans on a run page by kind, status, or tool.

The JSON API accepts the same filters.

```powershell
curl.exe "http://127.0.0.1:8000/api/runs/run-candidate-001?kind=tool&status=error"
```

The response keeps only matching spans. The run metrics still show full totals.

Use the drop-downs on a run page. Clear the filters to see every span.

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

The test suite covers schema validation, idempotent storage, WAL coordination, retry behavior, directory ingestion, handler config, side-effect guards, replay, comparison, search, filtering, saved comparisons, OTLP conversion, export files, CSV rendering, CLI, and API routes.

Run the checks with these commands.

```powershell
python -m pytest
ruff check .
python -m compileall agent_trace_workbench tests
```

Current verification passes 102 tests, Ruff lint, dependency checks, and Python compilation. CI runs these checks on Python 3.11, 3.12, and 3.13 for every push and pull request.

## Limitations

Replay does not call external tools. Unknown tools use their recorded results.

The guard trusts the declared side-effect level. It does not inspect the handler code.

Comparison aligns tool calls by recorded position. It does not infer semantic call identity.

Search uses SQL `LIKE` matching. It does not rank results by relevance.

OTLP import reads the JSON encoding only. It does not read protobuf binary files.

OTLP spans become internal spans unless they carry workbench kind attributes.

The exporter stores workbench fields as `workbench.*` attributes.

`run_id` derives from the service name and trace ID. It may differ from the producer's run label.

SQLite allows one writer at a time. A writer waits for the busy timeout, then the write retries and reports the error.

WAL mode creates `-wal` and `-shm` files beside the database.

The UI accepts one trace document per request. Use the CLI watcher for directory ingestion.

The watcher scans one directory level. It does not recurse into child directories.

The watcher uses file size and modification time. A rare same-size, same-time rewrite may wait for the next change.

OpenTelemetry spans cover workbench operations. The release does not export agent spans to a remote collector.

CSV exports keep arguments and results as JSON cells. Spreadsheet tools cannot index inside those cells.

## Roadmap

- Release 0.4 complete: add search, span filtering, and saved comparisons.
- Release 0.5 complete: add OTLP import and local export files.
- Release 0.6 complete: add coordination for shared databases.
- Release 0.7 complete: add richer comparison views and CSV export.
- Release 0.8: export workbench spans to a local OpenTelemetry collector.

## Repository map

`fixtures/` contains meaningful baseline and candidate traces. It also contains a handler config and demo scripts.

`tests/` contains deterministic tests for the core, shared-database coordination, guards, search, OTLP, export, CLI, and API.

`static/` and `templates/` contain the presentation layer.

`data/` is created at runtime and remains ignored by Git.

`requirements.lock` pins the direct dependencies used by local setup and CI.

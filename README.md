# Agent Trace Workbench

Agent Trace Workbench keeps agent-run evidence on your machine.

It records local JSON traces in SQLite. It replays tool calls with deterministic handlers. It compares runs by call order, timing, results, and outcomes.

Release 0.3 adds configurable replay handlers and side-effect guards.

## Value

Agent debugging needs evidence at tool boundaries.

This workbench makes each boundary visible. It shows inputs, outputs, timing, attributes, and errors in one local record.

The design supports repeatable review. A replay runs a registered local handler when available. It uses the recorded result when no handler exists.

No hosted service is required. The SQLite database stays in the local `data` directory.

The watcher supports a common handoff. An agent writes a JSON file. The workbench finds the file and records the run.

## Architecture

```text
local JSON trace
       |
       v
Pydantic contract ---> SQLite trace store ---> FastAPI views and JSON API
       ^                      |
       |                      +---------+---------+
directory watcher             v                   v
                        replay engine       comparison engine
                            |
                            v
                  handler config + guard
```

- `models.py` defines the portable trace contract.
- `handlers.py` loads local handler config and applies side-effect guards.
- `storage.py` owns the SQLite schema and idempotent ingestion.
- `ingestion.py` watches JSON files and returns stable schema error reports.
- `replay.py` runs guarded local handlers and records mismatches.
- `compare.py` aligns tool calls by recorded position.
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

The test suite covers schema validation, idempotent storage, directory ingestion, handler config, side-effect guards, deterministic replay, comparison, CLI routes, and API routes.

Run the checks with these commands.

```powershell
python -m pytest
ruff check .
python -m compileall agent_trace_workbench tests
```

Current verification passes 29 tests, Ruff lint, dependency checks, and Python compilation. CI runs these checks on Python 3.11, 3.12, and 3.13 for every push and pull request.

## Limitations

Replay does not call external tools. Unknown tools use their recorded results.

The guard trusts the declared side-effect level. It does not inspect the handler code.

Comparison aligns tool calls by recorded position. It does not infer semantic call identity.

SQLite is suitable for a local workbench. This release does not coordinate multiple writers.

The UI accepts one trace document per request. Use the CLI watcher for directory ingestion.

The watcher scans one directory level. It does not recurse into child directories.

The watcher uses file size and modification time. A rare same-size, same-time rewrite may wait for the next change.

OpenTelemetry spans cover workbench operations. The release does not export agent spans to a remote collector.

## Roadmap

- Release 0.3 complete: add configurable replay handlers and side-effect guards.
- Release 0.4: add span filtering, search, and saved comparisons.
- Release 0.5: add OTLP import and local export files.
- Release 0.6: add coordination for shared databases.

## Repository map

`fixtures/` contains meaningful baseline and candidate traces. It also contains a handler config and demo scripts.

`tests/` contains deterministic core, guard, CLI, and API tests.

`static/` and `templates/` contain the presentation layer.

`data/` is created at runtime and remains ignored by Git.

`requirements.lock` pins the direct dependencies used by local setup and CI.

# Agent Trace Workbench

Agent Trace Workbench keeps agent-run evidence on your machine.

It records local JSON traces in SQLite. It replays tool calls with deterministic handlers. It compares runs by call order, timing, results, and outcomes.

The first release provides a small FastAPI interface. It supports trace ingestion, run inspection, replay reports, and failure review.

## Value

Agent debugging needs evidence at tool boundaries.

This workbench makes each boundary visible. It shows inputs, outputs, timing, attributes, and errors in one local record.

The design supports repeatable review. A replay uses a registered local handler when available. It uses the recorded result when no handler exists.

No hosted service is required. The SQLite database stays in the local `data` directory.

## Architecture

```text
local JSON trace
       |
       v
Pydantic contract ---> SQLite trace store ---> FastAPI views and JSON API
                              |
                    +---------+---------+
                    v                   v
              replay engine       comparison engine
```

- `models.py` defines the portable trace contract.
- `storage.py` owns the SQLite schema and idempotent ingestion.
- `replay.py` runs deterministic local handlers and records mismatches.
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

The locked requirements file includes application and verification dependencies.

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

The test suite covers schema validation, idempotent storage, deterministic replay, comparison, and API routes.

Run the checks with these commands.

```powershell
python -m pytest
ruff check .
python -m compileall agent_trace_workbench tests
```

Current verification passes 11 tests, Ruff lint, and Python compilation. CI runs the same checks on every push and pull request.

## Limitations

Replay does not call external tools. Unknown tools use their recorded results.

Comparison aligns tool calls by recorded position. It does not infer semantic call identity.

SQLite is suitable for a local workbench. This release does not coordinate multiple writers.

The UI accepts one trace document per request. It does not watch a directory.

OpenTelemetry spans cover workbench operations. The release does not export agent spans to a remote collector.

## Roadmap

- Release 0.2: add directory watch ingestion with schema error reports.
- Release 0.3: add configurable replay handlers and side-effect guards.
- Release 0.4: add span filtering, search, and saved comparisons.
- Release 0.5: add OTLP import and local export files.

## Repository map

`fixtures/` contains meaningful baseline and candidate traces.

`tests/` contains deterministic core and API tests.

`static/` and `templates/` contain the presentation layer.

`data/` is created at runtime and remains ignored by Git.

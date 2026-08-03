"""FastAPI application for the Agent Trace Workbench."""

from __future__ import annotations

import json
import os
from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .compare import compare_runs
from .handlers import ReplayPolicy, load_handler_config
from .models import ComparisonCreate, TraceDocument
from .otlp import parse_otlp_json, trace_to_otlp_json
from .replay import ReplayEngine, default_replay_engine
from .storage import TraceStore
from .telemetry import configure_telemetry

ROOT = Path(__file__).resolve().parent.parent
try:
    templates = Jinja2Templates(directory=str(ROOT / "templates"))
except AssertionError:  # pragma: no cover - only used when Jinja2 is absent offline
    templates = None


def create_app(db_path: str | Path | None = None) -> FastAPI:
    """Create an isolated application instance for production or tests."""

    configure_telemetry()
    database_path = db_path or os.getenv("ATW_DB_PATH", "data/workbench.db")
    busy_timeout_ms = _env_int("ATW_DB_BUSY_TIMEOUT_MS", 5000)
    app = FastAPI(title="Agent Trace Workbench", version="0.6.0")
    app.state.store = TraceStore(database_path, busy_timeout_ms=busy_timeout_ms)
    app.state.replay_engine = _build_replay_engine()
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(
        request: Request,
        q: str | None = Query(default=None, max_length=200),
    ) -> Any:
        runs = app.state.store.search_runs(q) if q else app.state.store.list_runs()
        return render_template(
            request,
            "dashboard.html",
            {
                "runs": runs,
                "stats": _stats(runs),
                "query": q or "",
                "store": app.state.store.store_info(),
            },
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(
        request: Request,
        run_id: str,
        kind: str | None = Query(default=None),
        status: str | None = Query(default=None),
        tool: str | None = Query(default=None),
    ) -> Any:
        run = _get_run_or_404(app.state.store, run_id)
        filter_set = _span_filter_set(run, kind, status, tool)
        run = app.state.store.get_run(
            run_id, span_kind=kind, span_status=status, span_tool=tool
        )
        return render_template(
            request,
            "run.html",
            {"run": run, "filters": filter_set, "store": app.state.store.store_info()},
        )

    @app.get("/runs/{run_id}/replay", response_class=HTMLResponse)
    def replay_page(request: Request, run_id: str) -> Any:
        trace = _get_trace_or_404(app.state.store, run_id)
        report = app.state.replay_engine.replay(trace)
        context = {
            "run": app.state.store.get_run(run_id),
            "report": report.as_dict(),
            "store": app.state.store.store_info(),
        }
        return render_template(request, "replay.html", context)

    @app.get("/compare", response_class=HTMLResponse)
    def compare_page(
        request: Request,
        run_a: str | None = Query(default=None),
        run_b: str | None = Query(default=None),
    ) -> Any:
        runs = app.state.store.list_runs(100)
        report = None
        if run_a and run_b:
            trace_a = _get_trace_or_404(app.state.store, run_a)
            trace_b = _get_trace_or_404(app.state.store, run_b)
            report = compare_runs(trace_a, trace_b).as_dict()
        context = {
            "runs": runs,
            "report": report,
            "saved": app.state.store.list_comparisons(20),
            "selected_a": run_a,
            "selected_b": run_b,
            "store": app.state.store.store_info(),
        }
        return render_template(request, "compare.html", context)

    @app.get("/api/runs")
    def api_runs(
        limit: int = Query(default=20, ge=1, le=100),
        q: str | None = Query(default=None, max_length=200),
    ) -> list[dict[str, Any]]:
        if q:
            return app.state.store.search_runs(q, limit)
        return app.state.store.list_runs(limit)

    @app.get("/api/store")
    def api_store() -> dict[str, Any]:
        return app.state.store.store_info()

    @app.get("/api/runs/{run_id}")
    def api_run(
        run_id: str,
        kind: str | None = Query(default=None),
        status: str | None = Query(default=None),
        tool: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return _get_run_or_404(
            app.state.store, run_id, span_kind=kind, span_status=status, span_tool=tool
        )

    @app.post("/api/traces", status_code=201)
    def api_ingest(trace: TraceDocument, request: Request) -> dict[str, Any]:
        source_name = request.headers.get("x-trace-source", "api.json")
        return app.state.store.ingest(trace, source_name)

    @app.post("/api/otlp/traces", status_code=201)
    def api_otlp_ingest(payload: dict[str, Any], request: Request) -> list[dict[str, Any]]:
        source_name = request.headers.get("x-trace-source", "otlp.json")
        documents = parse_otlp_json(payload)
        if not documents:
            raise HTTPException(
                status_code=400, detail="No traces found in the OTLP payload"
            )
        return [
            app.state.store.ingest(trace, source_name) for trace in documents
        ]

    @app.get("/api/runs/{run_id}/export")
    def api_export_run(
        run_id: str,
        export_format: str = Query(default="json", alias="format"),
    ) -> Response:
        trace = _get_trace_or_404(app.state.store, run_id)
        if export_format == "otlp":
            payload = trace_to_otlp_json(trace)
            filename = f"{run_id}.otlp.json"
        elif export_format == "json":
            payload = trace.as_jsonable()
            filename = f"{run_id}.json"
        else:
            raise HTTPException(
                status_code=400, detail="format must be 'json' or 'otlp'"
            )
        content = json.dumps(payload, indent=2, default=str)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/runs/{run_id}/replay")
    def api_replay(run_id: str) -> dict[str, Any]:
        trace = _get_trace_or_404(app.state.store, run_id)
        return app.state.replay_engine.replay(trace).as_dict()

    @app.get("/api/compare")
    def api_compare(run_a: str, run_b: str) -> dict[str, Any]:
        trace_a = _get_trace_or_404(app.state.store, run_a)
        trace_b = _get_trace_or_404(app.state.store, run_b)
        return compare_runs(trace_a, trace_b).as_dict()

    @app.get("/api/comparisons")
    def api_list_comparisons(
        limit: int = Query(default=20, ge=1, le=100),
    ) -> list[dict[str, Any]]:
        return app.state.store.list_comparisons(limit)

    @app.post("/api/comparisons", status_code=201)
    def api_save_comparison(payload: ComparisonCreate) -> dict[str, Any]:
        trace_a = _get_trace_or_404(app.state.store, payload.run_a)
        trace_b = _get_trace_or_404(app.state.store, payload.run_b)
        report = compare_runs(trace_a, trace_b).as_dict()
        return app.state.store.save_comparison(
            payload.run_a, payload.run_b, payload.label, report
        )

    @app.get("/api/comparisons/{comparison_id}")
    def api_get_comparison(comparison_id: str) -> dict[str, Any]:
        comparison = app.state.store.get_comparison(comparison_id)
        if comparison is None:
            raise HTTPException(
                status_code=404, detail=f"Comparison not found: {comparison_id}"
            )
        return comparison

    @app.delete("/api/comparisons/{comparison_id}")
    def api_delete_comparison(comparison_id: str) -> dict[str, str]:
        if not app.state.store.delete_comparison(comparison_id):
            raise HTTPException(
                status_code=404, detail=f"Comparison not found: {comparison_id}"
            )
        return {"status": "deleted", "comparison_id": comparison_id}

    return app


def _build_replay_engine() -> ReplayEngine:
    """Build a replay engine from local environment configuration."""

    engine = default_replay_engine()
    config_path = os.getenv("ATW_HANDLERS_CONFIG")
    if config_path:
        config = load_handler_config(config_path)
        engine.load_config(config, base_dir=Path(config_path).parent)
    policy = os.getenv("ATW_REPLAY_POLICY")
    if policy:
        try:
            engine.policy = ReplayPolicy(policy)
        except ValueError as error:
            raise ValueError(
                f"ATW_REPLAY_POLICY must be one of "
                f"{', '.join(item.value for item in ReplayPolicy)}"
            ) from error
    return engine


def render_template(request: Request, name: str, context: dict[str, Any]) -> HTMLResponse:
    """Render the polished template or a small offline fallback."""

    if templates is not None:
        return templates.TemplateResponse(request=request, name=name, context=context)
    return HTMLResponse(_fallback_html(name, context))


def _fallback_html(name: str, context: dict[str, Any]) -> str:
    """Keep API route tests useful when optional presentation packages are unavailable."""

    if name == "dashboard.html":
        runs = context.get("runs", [])
        cards = "".join(
            f'<li><a href="/runs/{escape(run["run_id"])}">{escape(run["agent_name"])} '
            f'({escape(run["status"])})</a></li>'
            for run in runs
        )
        return f"<html><body><h1>Agent Trace Workbench</h1><ul>{cards}</ul></body></html>"
    if name == "run.html":
        run = context["run"]
        return f'<html><body><h1>{escape(run["agent_name"])}</h1></body></html>'
    if name == "replay.html":
        report = escape(str(context["report"]))
        return f"<html><body><h1>Replay report</h1><pre>{report}</pre></body></html>"
    return "<html><body><h1>Compare runs</h1></body></html>"


def _get_run_or_404(store: TraceStore, run_id: str, **filters: str | None) -> dict[str, Any]:
    run = store.get_run(run_id, **filters)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return run


def _get_trace_or_404(store: TraceStore, run_id: str) -> TraceDocument:
    trace = store.get_trace(run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return trace


def _stats(runs: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "runs": len(runs),
        "failures": sum(run["status"] == "error" for run in runs),
        "tools": sum(run.get("tool_count", 0) for run in runs),
    }


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


def _span_filter_set(
    run: dict[str, Any],
    kind: str | None,
    status: str | None,
    tool: str | None,
) -> dict[str, Any]:
    kinds = sorted({span["kind"] for span in run["spans"]})
    statuses = sorted({span["status"] for span in run["spans"]})
    tools = sorted(
        {span["tool_call"]["name"] for span in run["spans"] if span.get("tool_call")}
    )
    return {
        "kinds": kinds,
        "statuses": statuses,
        "tools": tools,
        "selected": {"kind": kind or "", "status": status or "", "tool": tool or ""},
        "active": any([kind, status, tool]),
    }


app = create_app()

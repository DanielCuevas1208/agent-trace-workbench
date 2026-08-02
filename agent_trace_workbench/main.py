"""FastAPI application for the Agent Trace Workbench."""

from __future__ import annotations

import os
from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .compare import compare_runs
from .models import TraceDocument
from .replay import default_replay_engine
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
    app = FastAPI(title="Agent Trace Workbench", version="0.1.0")
    app.state.store = TraceStore(database_path)
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> Any:
        runs = app.state.store.list_runs()
        return render_template(request, "dashboard.html", {"runs": runs, "stats": _stats(runs)})

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(request: Request, run_id: str) -> Any:
        run = _get_run_or_404(app.state.store, run_id)
        return render_template(request, "run.html", {"run": run})

    @app.get("/runs/{run_id}/replay", response_class=HTMLResponse)
    def replay_page(request: Request, run_id: str) -> Any:
        trace = _get_trace_or_404(app.state.store, run_id)
        report = default_replay_engine().replay(trace)
        context = {"run": app.state.store.get_run(run_id), "report": report.as_dict()}
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
        context = {"runs": runs, "report": report, "selected_a": run_a, "selected_b": run_b}
        return render_template(request, "compare.html", context)

    @app.get("/api/runs")
    def api_runs(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
        return app.state.store.list_runs(limit)

    @app.get("/api/runs/{run_id}")
    def api_run(run_id: str) -> dict[str, Any]:
        return _get_run_or_404(app.state.store, run_id)

    @app.post("/api/traces", status_code=201)
    def api_ingest(trace: TraceDocument, request: Request) -> dict[str, Any]:
        source_name = request.headers.get("x-trace-source", "api.json")
        return app.state.store.ingest(trace, source_name)

    @app.get("/api/runs/{run_id}/replay")
    def api_replay(run_id: str) -> dict[str, Any]:
        trace = _get_trace_or_404(app.state.store, run_id)
        return default_replay_engine().replay(trace).as_dict()

    @app.get("/api/compare")
    def api_compare(run_a: str, run_b: str) -> dict[str, Any]:
        trace_a = _get_trace_or_404(app.state.store, run_a)
        trace_b = _get_trace_or_404(app.state.store, run_b)
        return compare_runs(trace_a, trace_b).as_dict()

    return app


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


def _get_run_or_404(store: TraceStore, run_id: str) -> dict[str, Any]:
    run = store.get_run(run_id)
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


app = create_app()
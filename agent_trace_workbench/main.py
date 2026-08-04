"""FastAPI application for the Agent Trace Workbench."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from .collector import export_run_to_collector
from .compare import compare_runs
from .export import (
    comparison_to_csv,
    day_runs_to_csv,
    error_timeline_to_csv,
    report_to_csv,
    run_tools_to_csv,
    status_trend_to_csv,
    trend_overlay_to_csv,
    trend_to_csv,
)
from .handlers import ReplayPolicy, load_handler_config
from .models import (
    BulkLabelRequest,
    CollectorExportRequest,
    ComparisonCreate,
    RetentionRequest,
    RunAnnotations,
    TraceDocument,
)
from .otlp import parse_otlp_json, trace_to_otlp_json
from .replay import ReplayEngine, default_replay_engine
from .scheduler import CleanupScheduler
from .storage import TraceStore
from .telemetry import configure_telemetry, traces_url

ROOT = Path(__file__).resolve().parent.parent
try:
    templates = Jinja2Templates(directory=str(ROOT / "templates"))
except AssertionError:  # pragma: no cover - only used when Jinja2 is absent offline
    templates = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start and stop the optional retention scheduler with the server."""

    scheduler = getattr(app.state, "cleanup_scheduler", None)
    if scheduler is not None:
        scheduler.start()
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.stop()


def create_app(db_path: str | Path | None = None) -> FastAPI:
    """Create an isolated application instance for production or tests."""

    configure_telemetry()
    database_path = db_path or os.getenv("ATW_DB_PATH", "data/workbench.db")
    busy_timeout_ms = _env_int("ATW_DB_BUSY_TIMEOUT_MS", 5000)
    app = FastAPI(
        title="Agent Trace Workbench", version=__version__, lifespan=lifespan
    )
    app.state.store = TraceStore(database_path, busy_timeout_ms=busy_timeout_ms)
    app.state.replay_engine = _build_replay_engine()
    app.state.cleanup_scheduler = _build_cleanup_scheduler(app.state.store)
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(
        request: Request,
        q: str | None = Query(default=None, max_length=200),
        agent: str | None = Query(default=None, max_length=200),
        days: int = Query(default=14, ge=1, le=90),
        day: str | None = Query(default=None, max_length=10),
        compare: str | None = Query(default=None, max_length=200),
    ) -> Any:
        runs = app.state.store.search_runs(q) if q else app.state.store.list_runs()
        selected_agent = agent or ""
        compare_agent = compare or ""
        overlay = None
        if compare_agent and compare_agent != selected_agent:
            overlay = app.state.store.failure_trend_overlay(
                days,
                agent_name=selected_agent or None,
                compare_agent=compare_agent,
            )
            trend = overlay["primary"]
        else:
            trend = app.state.store.failure_trend(
                days, agent_name=selected_agent or None
            )
        chart = _trend_chart(
            trend,
            compare_trend=overlay["compare"] if overlay else None,
            compare_agent=compare_agent if overlay else "",
        )
        for point in chart["points"]:
            point["href"] = _day_href(selected_agent, days, point["day"])
        day_names = {point["day"] for point in chart["points"]}
        selected_day = day if day in day_names else None
        day_runs = (
            app.state.store.runs_on_day(
                selected_day, agent_name=selected_agent or None
            )
            if selected_day
            else None
        )
        status_breakdown = app.state.store.status_trend(
            days, agent_name=selected_agent or None
        )
        trend_agents = app.state.store.trend_agents()
        return render_template(
            request,
            "dashboard.html",
            {
                "runs": runs,
                "stats": _stats(runs),
                "trend": chart,
                "status_bars": _status_bars(status_breakdown),
                "query": q or "",
                "trend_agents": trend_agents,
                "compare_agents": (
                    [item for item in trend_agents if item != selected_agent]
                    if len(trend_agents) >= 2
                    else []
                ),
                "selected_agent": selected_agent,
                "compare_agent": compare_agent if overlay else "",
                "selected_day": selected_day,
                "day_runs": day_runs,
                "day_csv_link": (
                    _day_csv_href(selected_agent, selected_day)
                    if selected_day
                    else None
                ),
                "trend_links": _trend_links(selected_agent, days),
                "status_links": _status_links(selected_agent, days),
                "overlay_links": (
                    _overlay_links(selected_agent, compare_agent, days)
                    if overlay
                    else None
                ),
                "store": app.state.store.store_info(),
                "telemetry": _telemetry_info(),
                "scheduler": _scheduler_status(app),
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
        timeline = app.state.store.error_timeline(run_id)
        return render_template(
            request,
            "run.html",
            {
                "run": run,
                "filters": filter_set,
                "timeline": _error_timeline_chart(timeline) if timeline else None,
                "timeline_csv_link": f"/api/runs/{run_id}/timeline?format=csv",
                "store": app.state.store.store_info(),
                "telemetry": _telemetry_info(),
                "scheduler": _scheduler_status(app),
            },
        )

    @app.get("/runs/{run_id}/replay", response_class=HTMLResponse)
    def replay_page(request: Request, run_id: str) -> Any:
        trace = _get_trace_or_404(app.state.store, run_id)
        report = app.state.replay_engine.replay(trace)
        context = {
            "run": app.state.store.get_run(run_id),
            "report": report.as_dict(),
            "store": app.state.store.store_info(),
            "telemetry": _telemetry_info(),
            "scheduler": _scheduler_status(app),
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
            "telemetry": _telemetry_info(),
            "scheduler": _scheduler_status(app),
        }
        return render_template(request, "compare.html", context)

    @app.get("/review", response_class=HTMLResponse)
    def review_page(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
        status: Literal["ok", "error"] | None = Query(default=None),
    ) -> Any:
        runs = app.state.store.unreviewed_runs(limit, status=status)
        totals = app.state.store.library_report()["totals"]
        return render_template(
            request,
            "review.html",
            {
                "runs": runs,
                "totals": totals,
                "limit": limit,
                "status": status or "",
                "failure_count": sum(run["status"] == "error" for run in runs),
                "store": app.state.store.store_info(),
                "telemetry": _telemetry_info(),
                "scheduler": _scheduler_status(app),
            },
        )

    @app.get("/report", response_class=HTMLResponse)
    def report_page(
        request: Request,
        older_than_days: int = Query(default=30, ge=1, le=36500),
    ) -> Any:
        return render_template(
            request,
            "report.html",
            {
                "report": app.state.store.library_report(
                    older_than_days=older_than_days
                ),
                "store": app.state.store.store_info(),
                "telemetry": _telemetry_info(),
                "scheduler": _scheduler_status(app),
            },
        )

    @app.get("/cleanup", response_class=HTMLResponse)
    def cleanup_page(
        request: Request,
        older_than_days: int = Query(default=30, ge=1, le=36500),
        keep_labeled: str = Query(default="1"),
    ) -> Any:
        keep = _flag_on(keep_labeled)
        cutoff = _retention_cutoff(older_than_days)
        candidates = app.state.store.retention_candidates(
            cutoff, keep_labeled=keep
        )
        protected = app.state.store.protected_runs(cutoff) if keep else []
        return render_template(
            request,
            "cleanup.html",
            {
                "older_than_days": older_than_days,
                "keep_labeled": keep,
                "cutoff": cutoff,
                "candidate_runs": app.state.store.runs_by_ids(candidates),
                "protected_runs": len(protected),
                "library": app.state.store.library_report()["totals"],
                "history": app.state.store.sweep_history(10),
                "scheduler": _scheduler_status(app),
                "store": app.state.store.store_info(),
                "telemetry": _telemetry_info(),
            },
        )

    @app.get("/api/runs")
    def api_runs(
        limit: int = Query(default=20, ge=1, le=100),
        q: str | None = Query(default=None, max_length=200),
    ) -> list[dict[str, Any]]:
        if q:
            return app.state.store.search_runs(q, limit)
        return app.state.store.list_runs(limit)

    @app.get("/api/review")
    def api_review(
        limit: int = Query(default=20, ge=1, le=100),
        status: Literal["ok", "error"] | None = Query(default=None),
    ) -> list[dict[str, Any]]:
        return app.state.store.unreviewed_runs(limit, status=status)

    @app.post("/api/review/labels")
    def api_bulk_label(payload: BulkLabelRequest) -> dict[str, Any]:
        updated = app.state.store.bulk_set_labels(payload.run_ids, payload.label)
        return {"label": payload.label, "run_ids": payload.run_ids, "updated": updated}

    @app.post("/api/prune")
    def api_prune(payload: RetentionRequest) -> dict[str, Any]:
        cutoff = _retention_cutoff(payload.older_than_days)
        protected = (
            app.state.store.protected_runs(cutoff, run_ids=payload.run_ids)
            if payload.keep_labeled
            else []
        )
        if payload.dry_run:
            candidates = app.state.store.retention_candidates(
                cutoff,
                keep_labeled=payload.keep_labeled,
                run_ids=payload.run_ids,
            )
            result: dict[str, Any] = {
                "candidates": candidates,
                "deleted_runs": 0,
                "deleted_spans": 0,
                "deleted_comparisons": 0,
            }
        else:
            result = app.state.store.prune_runs(
                cutoff,
                keep_labeled=payload.keep_labeled,
                run_ids=payload.run_ids,
            )
        return {
            "older_than_days": payload.older_than_days,
            "cutoff": cutoff.isoformat(),
            "keep_labeled": payload.keep_labeled,
            "dry_run": payload.dry_run,
            "protected_runs": len(protected),
            **result,
        }

    @app.post("/api/cleanup")
    def api_cleanup(payload: RetentionRequest) -> dict[str, Any]:
        cutoff = _retention_cutoff(payload.older_than_days)
        protected = (
            app.state.store.protected_runs(cutoff, run_ids=payload.run_ids)
            if payload.keep_labeled
            else []
        )
        if payload.dry_run:
            candidates = app.state.store.retention_candidates(
                cutoff,
                keep_labeled=payload.keep_labeled,
                run_ids=payload.run_ids,
            )
            return {
                "older_than_days": payload.older_than_days,
                "cutoff": cutoff.isoformat(),
                "keep_labeled": payload.keep_labeled,
                "dry_run": True,
                "protected_runs": len(protected),
                "deleted_runs": 0,
                "deleted_spans": 0,
                "deleted_comparisons": 0,
                "candidates": candidates,
            }
        result = app.state.store.sweep_runs(
            payload.older_than_days,
            keep_labeled=payload.keep_labeled,
            run_ids=payload.run_ids,
        )
        return {
            "older_than_days": payload.older_than_days,
            "cutoff": result["cutoff"],
            "keep_labeled": payload.keep_labeled,
            "dry_run": False,
            "protected_runs": result["protected_runs"],
            "deleted_runs": result["deleted_runs"],
            "deleted_spans": result["deleted_spans"],
            "deleted_comparisons": result["deleted_comparisons"],
            "candidates": result["run_ids"],
            "sweep_id": result["sweep_id"],
            "ran_at": result["ran_at"],
        }

    @app.get("/api/cleanup/history")
    def api_cleanup_history(
        limit: int = Query(default=20, ge=1, le=100),
    ) -> list[dict[str, Any]]:
        return app.state.store.sweep_history(limit)

    @app.get("/api/cleanup/schedule")
    def api_cleanup_schedule() -> dict[str, Any]:
        return _scheduler_status(app)

    @app.get("/api/trend", response_model=None)
    def api_trend(
        days: int = Query(default=14, ge=1, le=90),
        agent: str | None = Query(default=None, max_length=200),
        export_format: str = Query(default="json", alias="format"),
    ) -> Response | list[dict[str, Any]]:
        trend = app.state.store.failure_trend(days, agent_name=agent)
        if export_format == "csv":
            return _download_response(
                trend_to_csv(trend, agent_name=agent or ""),
                "failure-trend.csv",
                "text/csv; charset=utf-8",
            )
        if export_format != "json":
            raise HTTPException(status_code=400, detail="format must be 'json' or 'csv'")
        return trend

    @app.get("/api/trend/agents")
    def api_trend_agents() -> list[str]:
        return app.state.store.trend_agents()

    @app.get("/api/trend/overlay", response_model=None)
    def api_trend_overlay(
        days: int = Query(default=14, ge=1, le=90),
        agent: str | None = Query(default=None, max_length=200),
        compare: str | None = Query(default=None, max_length=200),
        export_format: str = Query(default="json", alias="format"),
    ) -> Response | dict[str, Any]:
        if not compare or not compare.strip():
            raise HTTPException(
                status_code=400, detail="compare must name an agent"
            )
        if agent and compare == agent:
            raise HTTPException(
                status_code=400, detail="compare must differ from agent"
            )
        overlay = app.state.store.failure_trend_overlay(
            days, agent_name=agent, compare_agent=compare
        )
        if export_format == "csv":
            return _download_response(
                trend_overlay_to_csv(overlay),
                "failure-trend-overlay.csv",
                "text/csv; charset=utf-8",
            )
        if export_format != "json":
            raise HTTPException(status_code=400, detail="format must be 'json' or 'csv'")
        return overlay

    @app.get("/api/trend/statuses", response_model=None)
    def api_trend_statuses(
        days: int = Query(default=14, ge=1, le=90),
        agent: str | None = Query(default=None, max_length=200),
        export_format: str = Query(default="json", alias="format"),
    ) -> Response | list[dict[str, Any]]:
        buckets = app.state.store.status_trend(days, agent_name=agent)
        if export_format == "csv":
            return _download_response(
                status_trend_to_csv(buckets, agent_name=agent or ""),
                "status-trend.csv",
                "text/csv; charset=utf-8",
            )
        if export_format != "json":
            raise HTTPException(status_code=400, detail="format must be 'json' or 'csv'")
        return buckets

    @app.get("/api/trend/{day}", response_model=None)
    def api_trend_day(
        day: str,
        agent: str | None = Query(default=None, max_length=200),
        export_format: str = Query(default="json", alias="format"),
    ) -> Response | dict[str, Any]:
        try:
            runs = app.state.store.runs_on_day(day, agent_name=agent)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="day must use the YYYY-MM-DD format"
            ) from None
        if export_format == "csv":
            return _download_response(
                day_runs_to_csv(day, runs, agent_name=agent or ""),
                f"runs-{day}.csv",
                "text/csv; charset=utf-8",
            )
        if export_format != "json":
            raise HTTPException(status_code=400, detail="format must be 'json' or 'csv'")
        return {"day": day, "agent": agent or "", "runs": runs}

    @app.get("/api/report", response_model=None)
    def api_report(
        export_format: str = Query(default="json", alias="format"),
        older_than_days: int = Query(default=30, ge=1, le=36500),
    ) -> Response | dict[str, Any]:
        report = app.state.store.library_report(older_than_days=older_than_days)
        if export_format == "csv":
            filename = "library-report.csv"
            return _download_response(report_to_csv(report), filename, "text/csv; charset=utf-8")
        if export_format != "json":
            raise HTTPException(status_code=400, detail="format must be 'json' or 'csv'")
        return report

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

    @app.get("/api/runs/{run_id}/timeline", response_model=None)
    def api_run_timeline(
        run_id: str,
        export_format: str = Query(default="json", alias="format"),
    ) -> Response | dict[str, Any]:
        timeline = app.state.store.error_timeline(run_id)
        if timeline is None:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        if export_format == "csv":
            return _download_response(
                error_timeline_to_csv(timeline),
                f"{run_id}-error-timeline.csv",
                "text/csv; charset=utf-8",
            )
        if export_format != "json":
            raise HTTPException(status_code=400, detail="format must be 'json' or 'csv'")
        return timeline

    @app.get("/api/runs/{run_id}/spans/{span_id}")
    def api_run_span_detail(run_id: str, span_id: str) -> dict[str, Any]:
        detail = app.state.store.span_detail(run_id, span_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"Span not found: {span_id}")
        return detail

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
            media_type = "application/json"
        elif export_format == "json":
            payload = trace.as_jsonable()
            filename = f"{run_id}.json"
            media_type = "application/json"
        elif export_format == "csv":
            content = run_tools_to_csv(_get_run_or_404(app.state.store, run_id))
            return _download_response(content, f"{run_id}.csv", "text/csv; charset=utf-8")
        else:
            raise HTTPException(
                status_code=400, detail="format must be 'json', 'otlp', or 'csv'"
            )
        content = json.dumps(payload, indent=2, default=str)
        return _download_response(content, filename, media_type)

    @app.post("/api/runs/{run_id}/export/collector")
    def api_publish_run(
        run_id: str,
        payload: CollectorExportRequest | None = None,
    ) -> dict[str, Any]:
        trace = _get_trace_or_404(app.state.store, run_id)
        endpoint = _collector_endpoint(payload)
        return export_run_to_collector(trace, endpoint).as_dict()

    @app.get("/api/runs/{run_id}/replay")
    def api_replay(run_id: str) -> dict[str, Any]:
        trace = _get_trace_or_404(app.state.store, run_id)
        return app.state.replay_engine.replay(trace).as_dict()

    @app.get("/api/compare")
    def api_compare(
        run_a: str,
        run_b: str,
        export_format: str = Query(default="json", alias="format"),
    ) -> Response:
        trace_a = _get_trace_or_404(app.state.store, run_a)
        trace_b = _get_trace_or_404(app.state.store, run_b)
        report = compare_runs(trace_a, trace_b)
        if export_format == "csv":
            content = comparison_to_csv(report)
            filename = f"compare-{run_a}-vs-{run_b}.csv"
            return _download_response(content, filename, "text/csv; charset=utf-8")
        if export_format != "json":
            raise HTTPException(status_code=400, detail="format must be 'json' or 'csv'")
        payload = json.dumps(report.as_dict(), indent=2)
        return Response(content=payload, media_type="application/json")

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

    @app.patch("/api/runs/{run_id}/annotations")
    def api_update_annotations(run_id: str, payload: RunAnnotations) -> dict[str, Any]:
        run = app.state.store.update_annotations(
            run_id, label=payload.label, note=payload.note
        )
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        return run

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


def _build_cleanup_scheduler(store: TraceStore) -> CleanupScheduler | None:
    """Build the server-side retention scheduler from local environment config.

    The scheduler stays off until the operator sets
    ATW_CLEANUP_EVERY_SECONDS. Local cleanup stays opt-in, because a
    background delete must never surprise the person running the server.
    """

    every_seconds = _env_float("ATW_CLEANUP_EVERY_SECONDS", None)
    if every_seconds is None:
        return None
    older_than_days = _env_int("ATW_CLEANUP_OLDER_THAN_DAYS", 30)
    keep_labeled = _env_flag("ATW_CLEANUP_KEEP_LABELED", True)
    return CleanupScheduler(
        store,
        every_seconds=every_seconds,
        older_than_days=older_than_days,
        keep_labeled=keep_labeled,
    )


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
    if name == "review.html":
        runs = context.get("runs", [])
        cards = "".join(
            f'<li><a href="/runs/{escape(run["run_id"])}">{escape(run["agent_name"])} '
            f'({escape(run["status"])})</a></li>'
            for run in runs
        )
        return f"<html><body><h1>Review runs</h1><ul>{cards}</ul></body></html>"
    if name == "report.html":
        totals = escape(str(context["report"]["totals"]))
        return f"<html><body><h1>Library report</h1><pre>{totals}</pre></body></html>"
    if name == "cleanup.html":
        runs = context.get("candidate_runs", [])
        cards = "".join(
            f'<li><a href="/runs/{escape(run["run_id"])}">{escape(run["agent_name"])} '
            f'({escape(run["status"])})</a></li>'
            for run in runs
        )
        return f"<html><body><h1>Retention cleanup</h1><ul>{cards}</ul></body></html>"
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


def _download_response(content: str, filename: str, media_type: str) -> Response:
    """Wrap export text as an attachment download."""
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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


def _env_float(name: str, default: float | None) -> float | None:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off", "")


def _retention_cutoff(days: int) -> datetime:
    """Return the UTC instant before which a run counts as old."""

    return datetime.now(timezone.utc) - timedelta(days=days)


def _flag_on(value: str) -> bool:
    """Parse a query flag where absence means true."""

    return value.strip().lower() not in ("0", "false", "no", "off", "")


def _collector_endpoint(payload: CollectorExportRequest | None) -> str:
    """Resolve a collector endpoint from the request or the environment."""

    endpoint = (payload.endpoint if payload is not None else None) or os.getenv(
        "ATW_OTEL_COLLECTOR_ENDPOINT"
    )
    if not endpoint:
        raise HTTPException(
            status_code=400,
            detail="Provide an endpoint or set ATW_OTEL_COLLECTOR_ENDPOINT",
        )
    return endpoint


def _telemetry_info() -> dict[str, str]:
    """Return the active local span export settings for the page footer."""

    endpoint = os.getenv("ATW_OTEL_COLLECTOR_ENDPOINT")
    if endpoint:
        return {"collector_endpoint": endpoint, "collector_url": traces_url(endpoint)}
    return {"collector_endpoint": "", "collector_url": ""}


def _scheduler_status(app: FastAPI) -> dict[str, Any]:
    """Return the retention scheduler state for the footer and pages.

    A disabled scheduler keeps the same shape, so templates can render
    one layout whether the server sweeps in the background or not.
    """

    scheduler = app.state.cleanup_scheduler
    if scheduler is None:
        return {
            "enabled": False,
            "interval_seconds": None,
            "older_than_days": None,
            "keep_labeled": None,
            "last_sweep_at": None,
            "last_error": None,
        }
    return scheduler.status()


def _trend_chart(
    trend: list[dict[str, Any]],
    *,
    compare_trend: list[dict[str, Any]] | None = None,
    compare_agent: str = "",
) -> dict[str, Any]:
    """Shape a failure trend into SVG-ready data for the dashboard.

    The helper maps each day to a point on a fixed view box. It returns
    the point list, a few day labels, and the window totals so the
    template can draw one line chart without any charting dependency.
    Pass compare_trend and compare_agent to add a second line on the same
    time axis. Both series share one day window, so their points line up.
    """

    width = 680
    height = 150
    pad_x = 8
    pad_y = 10
    count = len(trend)
    step = (width - 2 * pad_x) / max(count - 1, 1)
    points = _trend_points(trend, width, height, pad_x, pad_y, step)
    label_indices = sorted({0, count - 1, count // 2}) if count else []
    labels = [
        {"x": points[index]["x"], "day": points[index]["day"]}
        for index in label_indices
        if index < count
    ]
    totals = _trend_totals(trend)
    chart: dict[str, Any] = {
        "width": width,
        "height": height,
        "days": count,
        "points": points,
        "labels": labels,
        "active_days": sum(1 for item in trend if item["runs"] > 0),
        "totals": totals,
    }
    if compare_trend is not None:
        compare_points = _trend_points(
            compare_trend, width, height, pad_x, pad_y, step
        )
        chart["compare"] = {
            "agent": compare_agent,
            "points": compare_points,
            "totals": _trend_totals(compare_trend),
        }
    return chart


def _trend_points(
    trend: list[dict[str, Any]],
    width: int,
    height: int,
    pad_x: int,
    pad_y: int,
    step: float,
) -> list[dict[str, Any]]:
    """Map each trend bucket to a point on the shared SVG view box."""

    points = []
    for index, item in enumerate(trend):
        x = round(pad_x + index * step, 2)
        rate = min(item["failure_rate"], 1.0)
        y = round(height - pad_y - rate * (height - 2 * pad_y), 2)
        points.append({**item, "x": x, "y": y})
    return points


def _trend_totals(trend: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the window totals for one failure trend series."""

    totals = {
        "runs": sum(item["runs"] for item in trend),
        "failures": sum(item["failures"] for item in trend),
    }
    totals["failure_rate"] = (
        round(totals["failures"] / totals["runs"], 4) if totals["runs"] else 0.0
    )
    return totals


def _trend_links(selected_agent: str, days: int) -> dict[str, str]:
    """Return the JSON and CSV export links for the dashboard trend panel.

    The links keep the active agent filter and any non-default window
    size, so a download matches what the panel draws. Agent names are
    URL-encoded because they may contain spaces or punctuation.
    """

    params: dict[str, Any] = {}
    if days != 14:
        params["days"] = days
    if selected_agent:
        params["agent"] = selected_agent
    if not params:
        return {"json": "/api/trend", "csv": "/api/trend?format=csv"}
    prefix = urlencode(params)
    return {
        "json": f"/api/trend?{prefix}",
        "csv": f"/api/trend?{prefix}&format=csv",
    }


def _status_links(selected_agent: str, days: int) -> dict[str, str]:
    """Return the JSON and CSV export links for the status breakdown panel.

    The links keep the active agent filter and any non-default window
    size, so a download matches what the panel draws. Agent names are
    URL-encoded because they may contain spaces or punctuation.
    """

    params: dict[str, Any] = {}
    if days != 14:
        params["days"] = days
    if selected_agent:
        params["agent"] = selected_agent
    if not params:
        return {"json": "/api/trend/statuses", "csv": "/api/trend/statuses?format=csv"}
    prefix = urlencode(params)
    return {
        "json": f"/api/trend/statuses?{prefix}",
        "csv": f"/api/trend/statuses?{prefix}&format=csv",
    }


def _overlay_links(selected_agent: str, compare_agent: str, days: int) -> dict[str, str]:
    """Return the JSON and CSV export links for the agent comparison overlay.

    The links keep the primary agent, the compare agent, and any
    non-default window size, so a download matches what the panel draws.
    Agent names are URL-encoded because they may contain spaces or
    punctuation.
    """

    params: dict[str, Any] = {"compare": compare_agent}
    if selected_agent:
        params["agent"] = selected_agent
    if days != 14:
        params["days"] = days
    prefix = urlencode(params)
    return {
        "json": f"/api/trend/overlay?{prefix}",
        "csv": f"/api/trend/overlay?{prefix}&format=csv",
    }


_STATUS_PRIORITY = {"ok": 0, "error": 1, "unset": 2}


def _status_order(statuses: list[str]) -> list[str]:
    """Return status names in a stable visual order.

    Known statuses sort by their priority so the stacked bars always
    draw ok at the bottom and error above it. Unknown statuses follow in
    alphabetical order, because an imported trace may carry a name the
    workbench does not recognise.
    """

    return sorted(statuses, key=lambda status: (_STATUS_PRIORITY.get(status, 3), status))


def _status_class(status: str) -> str:
    """Map a run status to a stable CSS color class."""

    return status if status in _STATUS_PRIORITY else "other"


def _status_bars(trend: list[dict[str, Any]]) -> dict[str, Any]:
    """Shape a status breakdown into SVG-ready stacked bars for the dashboard.

    The helper maps each day to a bar on a fixed view box. Each bar
    stacks one segment per status, scaled by the busiest day in the
    window. It returns the bar geometry and the legend totals so the
    template can draw the panel without any charting dependency.
    """

    width = 680
    height = 110
    pad_x = 8
    pad_y = 10
    count = len(trend)
    step = (width - 2 * pad_x) / max(count - 1, 1)
    bar_width = min(max(round(step * 0.55, 2), 6.0), 40.0)
    plot_height = height - 2 * pad_y
    max_total = max((item["runs"] for item in trend), default=0)
    bars = []
    for index, item in enumerate(trend):
        x = round(pad_x + index * step, 2)
        segments = []
        y_top = height - pad_y
        for status in _status_order(list(item["statuses"])):
            segment_count = item["statuses"][status]
            if max_total == 0:
                continue
            segment_height = round(segment_count / max_total * plot_height, 2)
            y0 = y_top
            y1 = round(y_top - segment_height, 2)
            y_top = y1
            segments.append(
                {
                    "status": status,
                    "cls": _status_class(status),
                    "count": segment_count,
                    "y0": y0,
                    "y1": y1,
                }
            )
        bars.append({"x": x, "day": item["day"], "runs": item["runs"], "segments": segments})
    status_names = sorted(
        {status for item in trend for status in item["statuses"]},
        key=lambda status: (_STATUS_PRIORITY.get(status, 3), status),
    )
    totals = {status: 0 for status in status_names}
    for item in trend:
        for status, segment_count in item["statuses"].items():
            totals[status] += segment_count
    legend = [
        {"status": status, "cls": _status_class(status), "total": totals[status]}
        for status in status_names
    ]
    return {
        "width": width,
        "height": height,
        "bar_width": bar_width,
        "bars": bars,
        "legend": legend,
    }


def _day_href(selected_agent: str, days: int, day: str) -> str:
    """Return the drill-down link for one day on the trend chart.

    The link keeps the window size and the agent filter, so the day view
    matches the panel that drew it. It anchors on the day panel below the
    chart.
    """

    params: dict[str, Any] = {"days": days, "day": day}
    if selected_agent:
        params["agent"] = selected_agent
    return f"/?{urlencode(params)}#trend-day"


def _day_csv_href(selected_agent: str, day: str) -> str:
    """Return the CSV download link for one day drill-down panel.

    The link keeps the active agent filter, so the file matches what the
    panel lists. Agent names are URL-encoded for the same reason as the
    trend export links.
    """

    params: dict[str, Any] = {"format": "csv"}
    if selected_agent:
        params["agent"] = selected_agent
    return f"/api/trend/{day}?{urlencode(params)}"


def _error_timeline_chart(timeline: dict[str, Any]) -> dict[str, Any]:
    """Shape an error timeline into SVG-ready data for the run page.

    The helper maps each failed span to a marker on a fixed view box.
    The marker position tracks the offset from the run start, so the
    panel shows when the failures happened on one time axis. The return
    value carries the marker geometry and the event details for the
    list below the chart.
    """

    width = 680
    height = 60
    pad_x = 16
    duration_ms = timeline.get("duration_ms", 0.0)
    plot_width = width - 2 * pad_x
    events = []
    for event in timeline["events"]:
        fraction = (
            min(max(event["start_offset_ms"] / duration_ms, 0.0), 1.0)
            if duration_ms > 0
            else 0.0
        )
        x = round(pad_x + fraction * plot_width, 2)
        events.append({**event, "x": x})
    return {
        "width": width,
        "height": height,
        "duration_ms": duration_ms,
        "error_count": timeline.get("error_count", 0),
        "has_errors": bool(events),
        "events": events,
    }


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

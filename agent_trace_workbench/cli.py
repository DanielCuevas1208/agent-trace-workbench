"""Command line interface for local trace workflows."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
from .ingestion import DirectoryWatcher, watch_directory
from .models import TraceDocument
from .otlp import parse_otlp_json, trace_to_otlp_json
from .replay import default_replay_engine
from .storage import TraceStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atw", description="Local agent trace workbench")
    parser.add_argument("--db", default="data/workbench.db", help="SQLite database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Ingest a local JSON trace")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--source", default=None)

    import_otlp = subparsers.add_parser(
        "import-otlp", help="Import an OTLP JSON trace file"
    )
    import_otlp.add_argument("path", type=Path)
    import_otlp.add_argument("--source", default=None)

    export = subparsers.add_parser("export", help="Export runs to portable JSON files")
    export.add_argument(
        "run_id", nargs="?", default=None, help="Run ID; omit to export every run"
    )
    export.add_argument(
        "--format", choices=["json", "otlp", "csv"], default="json", help="File format"
    )
    export.add_argument(
        "--output", type=Path, default=None, help="Output file or directory"
    )

    publish = subparsers.add_parser(
        "publish", help="Send recorded runs to a local collector"
    )
    publish.add_argument(
        "run_id",
        nargs="?",
        default=None,
        help="Run ID; omit to send every run",
    )
    publish.add_argument(
        "--endpoint",
        default=None,
        help="Collector OTLP/HTTP endpoint (default: ATW_OTEL_COLLECTOR_ENDPOINT)",
    )

    list_parser = subparsers.add_parser("list", help="List recent runs")
    list_parser.add_argument("--limit", type=int, default=20)

    subparsers.add_parser("store", help="Show the local store configuration")

    replay = subparsers.add_parser("replay", help="Replay a recorded run")
    replay.add_argument("run_id")
    replay.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Local JSON handler configuration",
    )
    replay.add_argument(
        "--policy",
        choices=[policy.value for policy in ReplayPolicy],
        default=None,
        help="Override the side-effect policy",
    )

    timeline = subparsers.add_parser(
        "timeline", help="Show the error timeline of one run"
    )
    timeline.add_argument("run_id")
    timeline.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Output format",
    )

    compare = subparsers.add_parser("compare", help="Compare two recorded runs")
    compare.add_argument("run_a")
    compare.add_argument("run_b")
    compare.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Output format",
    )

    search = subparsers.add_parser("search", help="Search recorded runs")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)

    comparisons = subparsers.add_parser("comparisons", help="Manage saved comparisons")
    comparisons.add_argument("--limit", type=int, default=20)
    comparisons.add_argument("--delete", default=None, help="Comparison ID to delete")

    review = subparsers.add_parser(
        "review", help="List runs that still need a review label"
    )
    review.add_argument("--limit", type=int, default=20)
    review.add_argument(
        "--label",
        default=None,
        help="Bulk label the listed runs, or every unreviewed run",
    )
    review.add_argument(
        "--run-id",
        action="append",
        dest="run_ids",
        default=None,
        help="Run ID to label; repeat for several runs",
    )

    report = subparsers.add_parser(
        "report", help="Print a folder-level summary of the local library"
    )
    report.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Output format",
    )
    report.add_argument(
        "--older-than",
        type=int,
        default=30,
        dest="older_than_days",
        help="Retention line age in days (default: 30)",
    )

    trend = subparsers.add_parser(
        "trend", help="Show the daily failure trend"
    )
    trend.add_argument(
        "--days",
        type=int,
        default=14,
        help="Window size in days (default: 14)",
    )
    trend.add_argument(
        "--agent",
        default=None,
        help="Restrict the trend to one agent name",
    )
    trend.add_argument(
        "--compare",
        default=None,
        help="Draw a second failure line for one agent comparison",
    )
    trend.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Output format",
    )
    trend.add_argument(
        "--agents",
        action="store_true",
        help="List the agent names available for filtering",
    )
    trend.add_argument(
        "--day",
        default=None,
        help="List the runs that started on one YYYY-MM-DD day",
    )
    trend.add_argument(
        "--statuses",
        action="store_true",
        help="Show the per-day run status breakdown",
    )

    annotate = subparsers.add_parser(
        "annotate", help="Label a run and add local review notes"
    )
    annotate.add_argument("run_id")
    annotate.add_argument("--label", default=None, help="Short label for the run")
    annotate.add_argument("--note", default=None, help="Free-text review note")
    annotate.add_argument(
        "--clear", action="store_true", help="Remove the label and the note"
    )

    watch = subparsers.add_parser("watch", help="Watch a directory for local JSON traces")
    watch.add_argument("directory", type=Path)
    watch.add_argument("--pattern", default="*.json")
    watch.add_argument("--interval", type=float, default=2.0, dest="interval_seconds")
    watch.add_argument("--once", action="store_true", help="Scan once and exit")

    prune = subparsers.add_parser(
        "prune", help="Delete runs last ingested before a retention cutoff"
    )
    prune.add_argument(
        "--older-than",
        type=int,
        default=30,
        dest="older_than_days",
        help="Delete runs older than this many days (default: 30)",
    )
    prune.add_argument(
        "--keep-labeled",
        dest="keep_labeled",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep runs that carry a label (default: on)",
    )
    prune.add_argument(
        "--run-id",
        action="append",
        dest="run_ids",
        default=None,
        help="Restrict cleanup to one run; repeat for several runs",
    )
    prune.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the candidate runs without deleting them",
    )

    cleanup = subparsers.add_parser(
        "cleanup",
        help="Run a scheduled retention cleanup and record it",
    )
    cleanup.add_argument(
        "--older-than",
        type=int,
        default=30,
        dest="older_than_days",
        help="Delete runs older than this many days (default: 30)",
    )
    cleanup.add_argument(
        "--keep-labeled",
        dest="keep_labeled",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep runs that carry a label (default: on)",
    )
    cleanup.add_argument(
        "--run-id",
        action="append",
        dest="run_ids",
        default=None,
        help="Restrict cleanup to one run; repeat for several runs",
    )
    cleanup.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the candidate runs without recording a sweep",
    )
    cleanup.add_argument(
        "--every",
        type=float,
        default=None,
        dest="every_seconds",
        help="Repeat the sweep every N seconds until interrupted",
    )
    cleanup.add_argument(
        "--history",
        action="store_true",
        help="List recorded cleanup sweeps instead of running one",
    )
    cleanup.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of sweeps to list with --history",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    store = TraceStore(args.db)
    if args.command == "ingest":
        trace = TraceDocument.model_validate_json(args.path.read_text(encoding="utf-8"))
        print(
            json.dumps(
                store.ingest(
                    trace,
                    args.source or args.path.name,
                    source_dir=str(args.path.parent),
                ),
                indent=2,
            )
        )
    elif args.command == "import-otlp":
        documents = parse_otlp_json(args.path.read_text(encoding="utf-8"))
        if not documents:
            raise SystemExit("No traces found in the OTLP payload")
        runs = [
            _run_summary(
                store.ingest(
                    trace,
                    args.source or args.path.name,
                    source_dir=str(args.path.parent),
                )
            )
            for trace in documents
        ]
        print(
            json.dumps(
                {
                    "source": str(args.path),
                    "imported_runs": len(runs),
                    "runs": runs,
                },
                indent=2,
            )
        )
    elif args.command == "export":
        run_ids = [args.run_id] if args.run_id else store.list_run_ids()
        if not run_ids:
            raise SystemExit("No runs to export")
        output = args.output or Path("data/exports")
        exported = []
        for run_id in run_ids:
            if args.format == "csv":
                run = store.get_run(run_id)
                if run is None:
                    raise SystemExit(f"Run not found: {run_id}")
                path = _write_csv_export(run, output, single=(len(run_ids) == 1))
            else:
                trace = store.get_trace(run_id)
                if trace is None:
                    raise SystemExit(f"Run not found: {run_id}")
                path = _write_export(trace, args.format, output, single=(len(run_ids) == 1))
            exported.append({"run_id": run_id, "format": args.format, "path": str(path)})
        print(json.dumps({"exported": exported}, indent=2))
    elif args.command == "publish":
        endpoint = args.endpoint or os.getenv("ATW_OTEL_COLLECTOR_ENDPOINT")
        if not endpoint:
            raise SystemExit("Set --endpoint or ATW_OTEL_COLLECTOR_ENDPOINT")
        run_ids = [args.run_id] if args.run_id else store.list_run_ids()
        if not run_ids:
            raise SystemExit("No runs to publish")
        reports = []
        for run_id in run_ids:
            trace = store.get_trace(run_id)
            if trace is None:
                raise SystemExit(f"Run not found: {run_id}")
            reports.append(export_run_to_collector(trace, endpoint).as_dict())
        print(json.dumps({"endpoint": endpoint, "exported_runs": reports}, indent=2))
    elif args.command == "list":
        print(json.dumps(store.list_runs(args.limit), indent=2))
    elif args.command == "store":
        print(json.dumps(store.store_info(), indent=2))
    elif args.command == "replay":
        trace = store.get_trace(args.run_id)
        if trace is None:
            raise SystemExit(f"Run not found: {args.run_id}")
        engine = default_replay_engine()
        if args.config is not None:
            config = load_handler_config(args.config)
            if args.policy is not None:
                config.policy = ReplayPolicy(args.policy)
            engine.load_config(config, base_dir=args.config.parent)
        elif args.policy is not None:
            engine.policy = ReplayPolicy(args.policy)
        print(json.dumps(engine.replay(trace).as_dict(), indent=2))
    elif args.command == "compare":
        trace_a = store.get_trace(args.run_a)
        trace_b = store.get_trace(args.run_b)
        if trace_a is None or trace_b is None:
            raise SystemExit("Both run IDs must exist")
        report = compare_runs(trace_a, trace_b)
        if args.format == "csv":
            print(comparison_to_csv(report), end="")
        else:
            print(json.dumps(report.as_dict(), indent=2))
    elif args.command == "timeline":
        timeline = store.error_timeline(args.run_id)
        if timeline is None:
            raise SystemExit(f"Run not found: {args.run_id}")
        if args.format == "csv":
            print(error_timeline_to_csv(timeline), end="")
        else:
            print(json.dumps(timeline, indent=2))
    elif args.command == "search":
        print(json.dumps(store.search_runs(args.query, args.limit), indent=2))
    elif args.command == "comparisons":
        if args.delete:
            deleted = store.delete_comparison(args.delete)
            if not deleted:
                raise SystemExit(f"Comparison not found: {args.delete}")
            print(json.dumps({"deleted": args.delete}, indent=2))
        else:
            print(json.dumps(store.list_comparisons(args.limit), indent=2))
    elif args.command == "review":
        if args.label is not None:
            _validate_annotation("label", args.label)
            run_ids = args.run_ids or [
                run["run_id"] for run in store.unreviewed_runs(100)
            ]
            if not run_ids:
                raise SystemExit("No runs to label")
            updated = store.bulk_set_labels(run_ids, args.label)
            print(json.dumps({"label": args.label, "updated": updated}, indent=2))
        elif args.run_ids:
            raise SystemExit("Provide --label together with --run-id")
        else:
            print(json.dumps(store.unreviewed_runs(args.limit), indent=2))
    elif args.command == "report":
        if args.older_than_days < 1:
            raise SystemExit("--older-than must be at least 1 day")
        report = store.library_report(older_than_days=args.older_than_days)
        if args.format == "csv":
            print(report_to_csv(report), end="")
        else:
            print(json.dumps(report, indent=2))
    elif args.command == "trend":
        if args.agents:
            print(json.dumps(store.trend_agents(), indent=2))
        elif args.day:
            try:
                runs = store.runs_on_day(args.day, agent_name=args.agent)
            except ValueError:
                raise SystemExit("--day must use the YYYY-MM-DD format") from None
            if args.format == "csv":
                print(day_runs_to_csv(args.day, runs, agent_name=args.agent or ""), end="")
            else:
                print(
                    json.dumps(
                        {"day": args.day, "agent": args.agent or "", "runs": runs},
                        indent=2,
                    )
                )
        elif args.days < 1 or args.days > 90:
            raise SystemExit("--days must be between 1 and 90")
        elif args.compare:
            if args.compare == (args.agent or ""):
                raise SystemExit("--compare must differ from --agent")
            overlay = store.failure_trend_overlay(
                args.days, agent_name=args.agent, compare_agent=args.compare
            )
            if args.format == "csv":
                print(trend_overlay_to_csv(overlay), end="")
            else:
                print(json.dumps(overlay, indent=2))
        elif args.statuses:
            buckets = store.status_trend(args.days, agent_name=args.agent)
            if args.format == "csv":
                print(status_trend_to_csv(buckets, agent_name=args.agent or ""), end="")
            else:
                print(json.dumps(buckets, indent=2))
        else:
            trend = store.failure_trend(args.days, agent_name=args.agent)
            if args.format == "csv":
                print(trend_to_csv(trend, agent_name=args.agent or ""), end="")
            else:
                print(json.dumps(trend, indent=2))
    elif args.command == "annotate":
        if args.clear:
            label = ""
            note = ""
        elif args.label is None and args.note is None:
            raise SystemExit("Provide --label, --note, or --clear")
        else:
            label = args.label
            note = args.note
        _validate_annotation("label", label)
        _validate_annotation("note", note)
        run = store.update_annotations(args.run_id, label=label, note=note)
        if run is None:
            raise SystemExit(f"Run not found: {args.run_id}")
        print(
            json.dumps(
                {
                    "run_id": run["run_id"],
                    "label": run["label"],
                    "note": run["note"],
                },
                indent=2,
            )
        )
    elif args.command == "watch":
        watcher = DirectoryWatcher(store, args.directory, args.pattern)
        watch_directory(watcher, args.interval_seconds, args.once)
    elif args.command == "prune":
        if args.older_than_days < 1:
            raise SystemExit("--older-than must be at least 1 day")
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.older_than_days)
        protected = (
            store.protected_runs(cutoff, run_ids=args.run_ids)
            if args.keep_labeled
            else []
        )
        if args.dry_run:
            candidates = store.retention_candidates(
                cutoff, keep_labeled=args.keep_labeled, run_ids=args.run_ids
            )
            print(
                json.dumps(
                    _prune_result(
                        args,
                        cutoff,
                        protected,
                        dry_run=True,
                        run_ids=candidates,
                        deleted_runs=0,
                        deleted_spans=0,
                        deleted_comparisons=0,
                    ),
                    indent=2,
                )
            )
        else:
            result = store.prune_runs(
                cutoff, keep_labeled=args.keep_labeled, run_ids=args.run_ids
            )
            print(
                json.dumps(
                    _prune_result(
                        args,
                        cutoff,
                        protected,
                        dry_run=False,
                        run_ids=result["candidates"],
                        deleted_runs=result["deleted_runs"],
                        deleted_spans=result["deleted_spans"],
                        deleted_comparisons=result["deleted_comparisons"],
                    ),
                    indent=2,
                )
            )
    elif args.command == "cleanup":
        if args.history:
            print(json.dumps(store.sweep_history(args.limit), indent=2))
        else:
            _run_cleanup_loop(store, args)


def _run_cleanup_loop(store: TraceStore, args: argparse.Namespace) -> None:
    """Run one retention sweep, repeating when --every requests a schedule.

    A scheduled cleanup suits cron, Task Scheduler, or a long-lived local
    process. Each pass prints one JSON record. A dry run never records a
    sweep in the cleanup log.
    """

    while True:
        print(json.dumps(_cleanup_result(store, args), indent=2))
        if not args.every_seconds:
            return
        try:
            time.sleep(args.every_seconds)
        except KeyboardInterrupt:
            return


def _cleanup_result(store: TraceStore, args: argparse.Namespace) -> dict[str, object]:
    if args.older_than_days < 1:
        raise SystemExit("--older-than must be at least 1 day")
    if args.dry_run:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.older_than_days)
        protected = (
            store.protected_runs(cutoff, run_ids=args.run_ids)
            if args.keep_labeled
            else []
        )
        candidates = store.retention_candidates(
            cutoff, keep_labeled=args.keep_labeled, run_ids=args.run_ids
        )
        return {
            "older_than_days": args.older_than_days,
            "cutoff": cutoff.isoformat(),
            "keep_labeled": args.keep_labeled,
            "dry_run": True,
            "protected_runs": len(protected),
            "deleted_runs": 0,
            "deleted_spans": 0,
            "deleted_comparisons": 0,
            "run_ids": candidates,
        }
    result = store.sweep_runs(
        args.older_than_days,
        keep_labeled=args.keep_labeled,
        run_ids=args.run_ids,
    )
    return {
        "older_than_days": args.older_than_days,
        "cutoff": result["cutoff"],
        "keep_labeled": args.keep_labeled,
        "dry_run": False,
        "protected_runs": result["protected_runs"],
        "deleted_runs": result["deleted_runs"],
        "deleted_spans": result["deleted_spans"],
        "deleted_comparisons": result["deleted_comparisons"],
        "run_ids": result["run_ids"],
        "sweep_id": result["sweep_id"],
        "ran_at": result["ran_at"],
    }


def _prune_result(
    args: argparse.Namespace,
    cutoff: datetime,
    protected: list[str],
    *,
    dry_run: bool,
    run_ids: list[str],
    deleted_runs: int,
    deleted_spans: int,
    deleted_comparisons: int,
) -> dict[str, object]:
    return {
        "older_than_days": args.older_than_days,
        "cutoff": cutoff.isoformat(),
        "keep_labeled": args.keep_labeled,
        "dry_run": dry_run,
        "protected_runs": len(protected),
        "deleted_runs": deleted_runs,
        "deleted_spans": deleted_spans,
        "deleted_comparisons": deleted_comparisons,
        "run_ids": run_ids,
    }


def _run_summary(run: dict[str, object]) -> dict[str, object]:
    return {
        "run_id": run["run_id"],
        "status": run["status"],
        "duration_ms": run["duration_ms"],
        "tool_count": run["tool_count"],
        "source_name": run["source_name"],
    }


def _validate_annotation(name: str, value: str | None) -> None:
    limits = {"label": 80, "note": 2000}
    if value is not None and len(value) > limits[name]:
        raise SystemExit(f"{name} must be at most {limits[name]} characters")


def _write_csv_export(run: dict[str, object], output: Path, *, single: bool) -> Path:
    suffix = ".csv"
    filename = f"{_safe_filename(str(run['run_id']))}{suffix}"
    if single and not _looks_like_directory(output, suffix):
        target = output
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        target = output / filename
        output.mkdir(parents=True, exist_ok=True)
    target.write_text(run_tools_to_csv(run), encoding="utf-8")
    return target


def _write_export(
    trace: TraceDocument,
    export_format: str,
    output: Path,
    *,
    single: bool,
) -> Path:
    suffix = ".otlp.json" if export_format == "otlp" else ".json"
    filename = f"{_safe_filename(trace.run_id)}{suffix}"
    if single and not _looks_like_directory(output, suffix):
        target = output
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        target = output / filename
        output.mkdir(parents=True, exist_ok=True)
    payload = trace_to_otlp_json(trace) if export_format == "otlp" else trace.as_jsonable()
    target.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return target


def _looks_like_directory(path: Path, suffix: str) -> bool:
    if path.exists():
        return path.is_dir()
    if path.suffix:
        return path.suffix != suffix
    return True


def _safe_filename(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "-", value).strip("-") or "run"


if __name__ == "__main__":
    main()

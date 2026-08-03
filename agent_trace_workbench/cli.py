"""Command line interface for local trace workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .compare import compare_runs
from .handlers import ReplayPolicy, load_handler_config
from .ingestion import DirectoryWatcher, watch_directory
from .models import TraceDocument
from .replay import default_replay_engine
from .storage import TraceStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atw", description="Local agent trace workbench")
    parser.add_argument("--db", default="data/workbench.db", help="SQLite database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Ingest a local JSON trace")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--source", default=None)

    list_parser = subparsers.add_parser("list", help="List recent runs")
    list_parser.add_argument("--limit", type=int, default=20)

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

    compare = subparsers.add_parser("compare", help="Compare two recorded runs")
    compare.add_argument("run_a")
    compare.add_argument("run_b")

    watch = subparsers.add_parser("watch", help="Watch a directory for local JSON traces")
    watch.add_argument("directory", type=Path)
    watch.add_argument("--pattern", default="*.json")
    watch.add_argument("--interval", type=float, default=2.0, dest="interval_seconds")
    watch.add_argument("--once", action="store_true", help="Scan once and exit")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    store = TraceStore(args.db)
    if args.command == "ingest":
        trace = TraceDocument.model_validate_json(args.path.read_text(encoding="utf-8"))
        print(json.dumps(store.ingest(trace, args.source or args.path.name), indent=2))
    elif args.command == "list":
        print(json.dumps(store.list_runs(args.limit), indent=2))
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
        print(json.dumps(compare_runs(trace_a, trace_b).as_dict(), indent=2))
    elif args.command == "watch":
        watcher = DirectoryWatcher(store, args.directory, args.pattern)
        watch_directory(watcher, args.interval_seconds, args.once)


if __name__ == "__main__":
    main()

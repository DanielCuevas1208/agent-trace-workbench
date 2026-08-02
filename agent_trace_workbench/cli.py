"""Command line interface for local trace workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .compare import compare_runs
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

    compare = subparsers.add_parser("compare", help="Compare two recorded runs")
    compare.add_argument("run_a")
    compare.add_argument("run_b")
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
        print(json.dumps(default_replay_engine().replay(trace).as_dict(), indent=2))
    elif args.command == "compare":
        trace_a = store.get_trace(args.run_a)
        trace_b = store.get_trace(args.run_b)
        if trace_a is None or trace_b is None:
            raise SystemExit("Both run IDs must exist")
        print(json.dumps(compare_runs(trace_a, trace_b).as_dict(), indent=2))


if __name__ == "__main__":
    main()

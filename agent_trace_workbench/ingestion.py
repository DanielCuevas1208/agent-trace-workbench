"""Deterministic polling ingestion for local trace directories."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import TraceDocument
from .storage import TraceStore
from .telemetry import traced_operation


@dataclass(frozen=True)
class IngestionIssue:
    """A file that the watcher could not validate or read."""

    source_name: str
    kind: str
    message: str

    def as_dict(self) -> dict[str, str]:
        """Return a JSON-compatible issue record."""

        return {
            "source_name": self.source_name,
            "kind": self.kind,
            "message": self.message,
        }


@dataclass(frozen=True)
class DirectoryIngestReport:
    """Summarise one watcher scan without hiding file-level errors."""

    directory: str
    pattern: str
    discovered_files: int
    processed_files: int
    skipped_files: int
    ingested_runs: list[dict[str, Any]]
    issues: list[IngestionIssue]

    @property
    def ingested_files(self) -> int:
        """Return the number of valid files stored during this scan."""

        return len(self.ingested_runs)

    def as_dict(self) -> dict[str, Any]:
        """Return a stable report for the CLI and future local clients."""

        return {
            "directory": self.directory,
            "pattern": self.pattern,
            "discovered_files": self.discovered_files,
            "processed_files": self.processed_files,
            "skipped_files": self.skipped_files,
            "ingested_files": self.ingested_files,
            "error_count": len(self.issues),
            "runs": self.ingested_runs,
            "errors": [issue.as_dict() for issue in self.issues],
        }


class DirectoryWatcher:
    """Poll a local directory and ingest new or changed JSON trace files."""

    def __init__(self, store: TraceStore, directory: str | Path, pattern: str = "*.json") -> None:
        self.store = store
        self.directory = Path(directory)
        if not self.directory.exists():
            raise FileNotFoundError(f"Trace directory does not exist: {self.directory}")
        if not self.directory.is_dir():
            raise NotADirectoryError(f"Trace path is not a directory: {self.directory}")
        if not pattern:
            raise ValueError("pattern must not be empty")
        self.pattern = pattern
        self._signatures: dict[Path, tuple[int, int]] = {}
        self._issues: dict[Path, IngestionIssue] = {}

    def scan(self) -> DirectoryIngestReport:
        """Ingest changed files and return errors for files that need attention."""

        with traced_operation(
            "ingestion.directory_scan",
            {"directory.name": self.directory.name, "file.pattern": self.pattern},
        ):
            candidates = sorted(
                (
                    path
                    for path in self.directory.iterdir()
                    if path.is_file() and fnmatchcase(path.name, self.pattern)
                ),
                key=lambda path: (path.name.casefold(), path.name),
            )
            current_signatures: dict[Path, tuple[int, int]] = {}
            current_issues: dict[Path, IngestionIssue] = {}
            ingested_runs: list[dict[str, Any]] = []
            issues: list[IngestionIssue] = []
            processed_files = 0
            skipped_files = 0

            for path in candidates:
                signature = _file_signature(path)
                if signature is None:
                    processed_files += 1
                    issues.append(
                        IngestionIssue(path.name, "read_error", "The file could not be inspected.")
                    )
                    continue

                current_signatures[path] = signature
                if self._signatures.get(path) == signature:
                    if issue := self._issues.get(path):
                        issues.append(issue)
                        current_issues[path] = issue
                        skipped_files += 1
                        continue
                    skipped_files += 1
                    continue

                processed_files += 1
                try:
                    trace = _load_trace(path)
                except _TraceFileError as error:
                    issue = IngestionIssue(path.name, error.kind, error.message)
                    issues.append(issue)
                    current_issues[path] = issue
                    continue

                run = self.store.ingest(trace, path.name)
                ingested_runs.append(
                    {
                        "run_id": run["run_id"],
                        "status": run["status"],
                        "duration_ms": run["duration_ms"],
                        "tool_count": run["tool_count"],
                        "source_name": run["source_name"],
                    }
                )

            self._signatures = current_signatures
            self._issues = current_issues
            return DirectoryIngestReport(
                directory=str(self.directory),
                pattern=self.pattern,
                discovered_files=len(candidates),
                processed_files=processed_files,
                skipped_files=skipped_files,
                ingested_runs=ingested_runs,
                issues=issues,
            )


class _TraceFileError(Exception):
    """Internal error with a stable category for directory reports."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def _load_trace(path: Path) -> TraceDocument:
    try:
        payload = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise _TraceFileError("read_error", "The file could not be read.") from error

    try:
        document = json.loads(payload)
    except json.JSONDecodeError as error:
        raise _TraceFileError(
            "invalid_json",
            f"Line {error.lineno}, column {error.colno}: {error.msg}.",
        ) from error

    try:
        return TraceDocument.model_validate(document)
    except ValidationError as error:
        detail = error.errors(include_url=False)[0]
        location = ".".join(str(part) for part in detail["loc"]) or "document"
        raise _TraceFileError("schema_error", f"{location}: {detail['msg']}.") from error


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def watch_directory(
    watcher: DirectoryWatcher,
    interval_seconds: float = 2.0,
    once: bool = False,
) -> None:
    """Poll a watcher until interrupted, or run one scan when once is true."""

    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be greater than zero")

    while True:
        print(json.dumps(watcher.scan().as_dict(), indent=2))
        if once:
            return
        try:
            time.sleep(interval_seconds)
        except KeyboardInterrupt:
            return

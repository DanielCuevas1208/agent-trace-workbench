"""Server-side retention sweeps for the workbench process.

The workbench can run a retention sweep on an interval inside the server
process. A background thread calls the same sweep the CLI runs, so the
cleanup log stays current while the server stays open. The scheduler is
opt-in. It starts only when the server sees ATW_CLEANUP_EVERY_SECONDS.
Each pass uses one short-lived database connection, so the scheduler
coexists with the API and the watcher on the same WAL database.
"""

from __future__ import annotations

import threading
from typing import Any

from .storage import TraceStore


class CleanupScheduler:
    """Run retention sweeps on an interval from one background thread."""

    def __init__(
        self,
        store: TraceStore,
        *,
        every_seconds: float,
        older_than_days: int = 30,
        keep_labeled: bool = True,
    ) -> None:
        if every_seconds <= 0:
            raise ValueError("every_seconds must be positive")
        if older_than_days < 1:
            raise ValueError("older_than_days must be at least 1")
        self.store = store
        self.every_seconds = every_seconds
        self.older_than_days = older_than_days
        self.keep_labeled = keep_labeled
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error: str | None = None

    @property
    def enabled(self) -> bool:
        """Return whether the background thread is running."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the sweep loop on a daemon thread."""
        if self.enabled:
            return
        self._stop.clear()
        self._last_error = None
        self._thread = threading.Thread(
            target=self._run, name="atw-cleanup-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the sweep loop and wait for the current pass to finish."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def sweep(self) -> dict[str, Any] | None:
        """Run one retention sweep and return the recorded result.

        A failed pass records the error and returns None. The scheduler
        survives the failure and tries again on the next interval.
        """
        try:
            return self.store.sweep_runs(
                self.older_than_days, keep_labeled=self.keep_labeled
            )
        except Exception as exc:  # noqa: BLE001 - one bad pass must not stop the loop
            self._last_error = str(exc)
            return None

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sweep()
            self._stop.wait(self.every_seconds)

    def status(self) -> dict[str, Any]:
        """Return the active schedule and the most recent sweep state."""
        last = self.store.last_sweep()
        return {
            "enabled": self.enabled,
            "interval_seconds": self.every_seconds,
            "older_than_days": self.older_than_days,
            "keep_labeled": self.keep_labeled,
            "last_sweep_at": last["ran_at"] if last else None,
            "last_error": self._last_error,
        }

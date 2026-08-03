"""Verify that requirements.txt and requirements.lock stay consistent.

The script reads pyproject.toml and requirements.txt at the repository
root. It fails when a project dependency is missing from the pinned
requirements file, or when any pinned line floats without an exact
version. It also checks the lockfile: every direct pin must appear in
requirements.lock, and the lockfile lines must stay sorted and pinned.
CI runs this script so dependency pins cannot drift silently.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    pins = _read_pins(ROOT / "requirements.txt")
    if not pins:
        print("requirements.txt must pin at least one dependency.", file=sys.stderr)
        return 1
    missing = [dep for dep in project["project"]["dependencies"] if dep not in pins]
    if missing:
        print(
            f"pyproject dependencies missing from requirements.txt: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1
    lock_path = ROOT / "requirements.lock"
    lock = {_normalize_pin(pin) for pin in _read_pins(lock_path)}
    if not lock:
        print("requirements.lock must pin at least one dependency.", file=sys.stderr)
        return 1
    unpinned_lock = [dep for dep in pins if _normalize_pin(dep) not in lock]
    if unpinned_lock:
        print(
            f"requirements.txt dependencies missing from requirements.lock: "
            f"{', '.join(unpinned_lock)}",
            file=sys.stderr,
        )
        return 1
    if _read_lines(lock_path) != sorted(_read_lines(lock_path), key=str.casefold):
        print("requirements.lock must list dependencies in sorted order.", file=sys.stderr)
        return 1
    print(f"Verified {len(pins)} direct pins and {len(lock)} locked dependencies.")
    return 0


def _read_pins(path: Path) -> set[str]:
    pins: set[str] = set()
    for line in _read_lines(path):
        if "==" not in line:
            raise SystemExit(f"Unpinned dependency in {path.name}: {line}")
        pins.add(line)
    return pins


def _normalize_pin(pin: str) -> str:
    name, version = pin.split("==", 1)
    name = name.split("[", 1)[0].strip().lower()
    return f"{name}=={version.strip()}"


def _read_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


if __name__ == "__main__":
    raise SystemExit(main())

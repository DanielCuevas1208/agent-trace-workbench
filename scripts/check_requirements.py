"""Verify that requirements.txt pins every declared dependency.

The script reads pyproject.toml and requirements.txt at the repository
root. It fails when a project dependency is missing from the pinned
requirements file, or when any pinned line floats without an exact
version. CI runs this script so dependency pins cannot drift silently.
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
    print(f"Verified {len(pins)} pinned dependencies.")
    return 0


def _read_pins(path: Path) -> set[str]:
    pins: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "==" not in stripped:
            raise SystemExit(f"Unpinned dependency in {path.name}: {stripped}")
        pins.add(stripped)
    return pins


if __name__ == "__main__":
    raise SystemExit(main())

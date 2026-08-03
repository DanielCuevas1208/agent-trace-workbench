import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_requirements_txt_pins_all_project_dependencies():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    locked = _read_pins(ROOT / "requirements.txt")
    assert locked, "requirements.txt must pin at least one dependency"
    missing = [dep for dep in project["project"]["dependencies"] if dep not in locked]
    assert missing == [], f"pyproject dependencies missing from requirements.txt: {missing}"


def test_requirements_lock_covers_every_direct_pin():
    direct = _read_pins(ROOT / "requirements.txt")
    lock = {_normalize_pin(line) for line in _read_lines(ROOT / "requirements.lock")}
    assert lock, "requirements.lock must pin at least one dependency"
    missing = [dep for dep in direct if _normalize_pin(dep) not in lock]
    assert (
        missing == []
    ), f"requirements.txt dependencies missing from requirements.lock: {missing}"


def test_requirements_lock_is_sorted_and_pinned():
    lines = _read_lines(ROOT / "requirements.lock")
    assert lines == sorted(lines, key=str.casefold), "lockfile must stay sorted"
    assert all("==" in line for line in lines), "lockfile lines must be pinned"
    assert len(lines) == len(set(lines)), "lockfile must not repeat a dependency"


def _read_pins(path: Path) -> set[str]:
    pins = set()
    for line in _read_lines(path):
        if "==" not in line:
            raise AssertionError(f"Unpinned dependency in {path.name}: {line}")
        pins.add(line)
    return pins


def _normalize_pin(pin: str) -> str:
    name, version = pin.split("==", 1)
    name = name.split("[", 1)[0].strip().lower()
    return f"{name}=={version.strip()}"


def _read_lines(path: Path) -> list[str]:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines

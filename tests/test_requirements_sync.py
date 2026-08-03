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


def _read_pins(path: Path) -> set[str]:
    pins = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "==" not in stripped:
            raise AssertionError(f"Unpinned dependency in {path.name}: {stripped}")
        pins.add(stripped)
    return pins

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_requirements_lock_matches_project_dependencies():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    project_deps = project["project"]["dependencies"]
    locked_deps = {
        tuple(line.partition("==")[::2])
        for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#") and not line.startswith("-r")
    }
    missing = [dep for dep in project_deps if tuple(dep.split("==")) not in locked_deps]
    assert missing == [], f"pyproject dependencies missing from requirements.lock: {missing}"

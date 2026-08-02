from pathlib import Path
from uuid import uuid4

import pytest

from agent_trace_workbench.models import TraceDocument

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture
def tmp_path(request) -> Path:
    path = Path.cwd() / ".pytest-tmp" / f"{request.node.name}-{uuid4().hex}"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def baseline() -> TraceDocument:
    return TraceDocument.model_validate_json((FIXTURES / "run_baseline.json").read_text())


@pytest.fixture
def candidate() -> TraceDocument:
    return TraceDocument.model_validate_json((FIXTURES / "run_candidate.json").read_text())
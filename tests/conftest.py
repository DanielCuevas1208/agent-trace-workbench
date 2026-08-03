from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from uuid import uuid4

import pytest

from agent_trace_workbench.models import TraceDocument

FIXTURES = Path(__file__).parents[1] / "fixtures"


class _CollectorRecorder:
    """Record OTLP JSON requests for one local stub collector."""

    def __init__(self) -> None:
        self.bodies: list[tuple[str, bytes]] = []
        self.status = 200


class _CollectorHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length)
        self.server.recorder.bodies.append((self.path, body))
        self.send_response(self.server.recorder.status)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_args) -> None:
        pass


@pytest.fixture
def collector_server():
    recorder = _CollectorRecorder()
    server = HTTPServer(("127.0.0.1", 0), _CollectorHandler)
    server.recorder = recorder
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    yield endpoint, recorder
    server.shutdown()
    thread.join(timeout=2)
    server.server_close()


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
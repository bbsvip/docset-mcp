"""Offline checks for the Docker integration and transport dispatch (no Docker daemon required)."""
from pathlib import Path
import sys
import types
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "source"))

import app
import mcp_server


def test_serve_http_cli(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "run_server", lambda **kw: calls.append(kw))
    monkeypatch.setattr(sys, "argv", ["app.py", "serve-http", "--host", "0.0.0.0", "--port", "8765"])
    app.main()
    assert calls == [{"transport": "streamable-http", "host": "0.0.0.0", "port": 8765}]


def test_serve_stdio_cli(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "run_server", lambda **kw: calls.append(kw))
    monkeypatch.setattr(sys, "argv", ["app.py", "serve"])
    app.main()
    assert calls == [{"transport": "stdio"}]


def test_http_server_mcp_sdk_dispatch(monkeypatch, tmp_path):
    """Capture SDK v2 call args with a fake server to avoid installing dependencies."""
    from docsets import DocsetStore
    records = []

    class FakeSettings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeMCP:
        def __init__(self, *args, **kwargs):
            self.tools = {}
            self.prompts = {}

        def tool(self):
            def decorator(func):
                self.tools[func.__name__] = func
                return func
            return decorator

        def prompt(self):
            def decorator(func):
                self.prompts[func.__name__] = func
                return func
            return decorator

        def run(self, **kwargs):
            records.append(kwargs)
            assert "search_docs" in self.tools
            assert "read_doc" in self.tools
            assert "documentation_research" in self.prompts
            assert "list_installed" in self.prompts["documentation_research"]("Python", "Check json.loads")

    # Intercept DocsetStore construction while retaining the real core.
    original = mcp_server.DocsetStore
    monkeypatch.setattr(mcp_server, "DocsetStore", lambda: original(tmp_path / "data"))
    fake_mcp = types.ModuleType("mcp")
    fake_server = types.ModuleType("mcp.server")
    fake_transport = types.ModuleType("mcp.server.transport_security")
    fake_server.MCPServer = FakeMCP
    fake_transport.TransportSecuritySettings = FakeSettings
    monkeypatch.setitem(sys.modules, "mcp", fake_mcp)
    monkeypatch.setitem(sys.modules, "mcp.server", fake_server)
    monkeypatch.setitem(sys.modules, "mcp.server.transport_security", fake_transport)
    mcp_server.run_server(transport="streamable-http", host="0.0.0.0", port=8765)
    assert records[0]["transport"] == "streamable-http"
    assert records[0]["stateless_http"] and records[0]["json_response"]
    assert records[0]["host"] == "0.0.0.0" and records[0]["port"] == 8765
    assert "localhost:*" in records[0]["transport_security"].kwargs["allowed_hosts"]


def test_docker_files_and_mounts():
    dockerfile = (ROOT / "Dockerfile").read_text()
    compose = (ROOT / "compose.yaml").read_text()
    ignore = (ROOT / ".dockerignore").read_text()
    assert 'ENTRYPOINT ["python", "/app/source/app.py"]' in dockerfile
    assert 'CMD ["serve-http",' in dockerfile
    assert 'USER 10001:10001' in dockerfile
    assert '127.0.0.1:${DOCSET_PORT:-8765}:8765' in compose
    assert 'name: docset-agent-mcp-data' in compose
    assert 'profiles: ["bulk"]' in compose
    assert 'venv/' in ignore and '.venv/' in ignore and 'data/' in ignore

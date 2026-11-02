"""Regression tests for automatic on-demand downloads, FTS, and catalog refresh."""
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "source"))
import app
import docsets
import mcp_server
from test_docsets import make_archive


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCSET_AUTO_INSTALL", "missing")
    monkeypatch.setenv("DOCSET_AUTO_INDEX_CONTENT", "true")
    monkeypatch.setenv("DOCSET_REFRESH_CATALOG_PERIODICALLY", "true")
    monkeypatch.setenv("DOCSET_CATALOG_REFRESH_HOURS", "24")
    store = docsets.DocsetStore(tmp_path / "data")
    store.catalog_file.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "complete_catalog": True,
        "entries": [{"id": "com.kapeli.dash:Python_3", "name": "Python_3",
                     "source": "com.kapeli.dash", "versions": ["3.14"], "latest": "3.14"}]
    }), encoding="utf-8")
    archive = tmp_path / "python.tgz"
    make_archive(archive)
    calls = []

    def fake_fetch(url, part):
        calls.append(url)
        return part.write_bytes(archive.read_bytes())

    monkeypatch.setattr(store, "_download_stream", fake_fetch)
    return store, calls


def test_auto_install_only_with_explicit_filter(configured):
    store, calls = configured
    assert store.search("json.loads") == []
    assert calls == []
    found = store.search("json.loads", "Python_3")
    assert len(found) == 1
    assert len(calls) == 1
    assert store.search("json.loads", "Python_3")[0]["symbol"] == "json.loads"
    assert len(calls) == 1
    assert store.search_content("Deserialize JSON", "Python_3")
    assert store._fts_path("com.kapeli.dash:Python_3").is_file()


def test_auto_install_on_read_with_exact_reference(configured):
    store, calls = configured
    page = store.read_ref("com.kapeli.dash:Python_3::library/json.html#json.loads")
    assert "Deserialize JSON" in page["content"]
    assert len(calls) == 1


def test_auto_install_on_content_search(configured):
    store, calls = configured
    found = store.search_content("Deserialize JSON", "Python_3")
    assert len(found) == 1
    assert len(calls) == 1


def test_auto_install_invalid_docset_never_downloads(configured):
    store, calls = configured
    with pytest.raises(ValueError, match="not in catalog"):
        store.search("anything", "NoSuchFramework")
    assert not calls


def test_disabled_auto_install_keeps_old_behavior(configured):
    store, calls = configured
    store.auto_install = "off"
    with pytest.raises(ValueError, match="auto-install disabled"):
        store.search("json.loads", "Python_3")
    assert calls == []


def test_index_after_explicit_download_and_backfill(configured):
    store, calls = configured
    download = store.download_one("Python_3")
    assert download["content_index"]["pages"] == 1
    assert store.search_content("Deserialize JSON", "Python_3")
    assert store.index_content_all(missing_only=True)["skipped"] == 1
    key = "com.kapeli.dash:Python_3"
    store._fts_path(key).unlink()
    assert store.index_content_all(missing_only=True)["indexed"] == 1
    assert len(calls) == 1


def test_on_demand_fts_of_previously_installed_docset(configured):
    store, _ = configured
    store.auto_index_content = False
    store.download_one("Python_3")
    assert not store._fts_path("com.kapeli.dash:Python_3").exists()
    store.auto_index_content = True
    assert store.search_content("Deserialize JSON", "Python_3")


def test_offline_cached_catalog_preserved(configured, monkeypatch):
    store, _ = configured
    assert store.refresh_catalog_if_due()["status"] == "fresh"
    payload = json.loads(store.catalog_file.read_text())
    payload["fetched_at"] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    store.catalog_file.write_text(json.dumps(payload), encoding="utf-8")

    def fail():
        raise RuntimeError("offline")

    monkeypatch.setattr(store, "_sync_catalog_unlocked", fail)
    result = store.refresh_catalog_if_due()
    assert result["status"] == "failed" and result["cached"] is True
    assert store.catalog_search("Python")["total"] == 1


def test_stale_catalog_only_refreshes_once(configured, monkeypatch):
    store, _ = configured
    data = json.loads(store.catalog_file.read_text())
    data["fetched_at"] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    store.catalog_file.write_text(json.dumps(data), encoding="utf-8")
    called = []

    def refresh():
        called.append(1)
        data["fetched_at"] = datetime.now(timezone.utc).isoformat()
        store.catalog_file.write_text(json.dumps(data), encoding="utf-8")
        return {"count": 1}

    monkeypatch.setattr(store, "_sync_catalog_unlocked", refresh)
    assert store.refresh_catalog_if_due()["status"] == "updated"
    assert store.refresh_catalog_if_due()["status"] == "fresh"
    assert len(called) == 1


def test_auto_fts_error_does_not_lose_symbol_index(configured, monkeypatch):
    store, _ = configured
    monkeypatch.setattr(store, "index_content", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    result = store.download_one("Python_3")
    assert "disk full" in result["content_index_warning"]
    assert store.search("json.loads", "Python_3")


def test_env_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCSET_AUTO_INSTALL", "everything")
    with pytest.raises(ValueError, match="DOCSET_AUTO_INSTALL"):
        docsets.DocsetStore(tmp_path / "invalid")
    monkeypatch.setenv("DOCSET_AUTO_INSTALL", "off")
    monkeypatch.setenv("DOCSET_AUTO_INDEX_CONTENT", "maybe")
    with pytest.raises(ValueError, match="DOCSET_AUTO_INDEX_CONTENT"):
        docsets.DocsetStore(tmp_path / "invalid2")


def test_mcp_maintenance_disabled_by_config(tmp_path, monkeypatch):
    store = docsets.DocsetStore(tmp_path / "data")
    monkeypatch.setattr(mcp_server.threading, "Thread", lambda *a, **k: pytest.fail("background task not needed"))
    mcp_server.start_background_maintenance(store)


def test_index_content_cli_all(monkeypatch, capsys):
    class FakeStore:
        def index_content_all(self, missing_only=False, log=None):
            assert missing_only is True
            return {"selected": 2, "indexed": 1, "skipped": 1, "failed": 0}
    monkeypatch.setattr(app, "DocsetStore", FakeStore)
    monkeypatch.setattr(sys, "argv", ["app.py", "index-content", "--all", "--missing-only"])
    app.main()
    assert '"indexed": 1' in capsys.readouterr().out


def test_concurrent_auto_installs_use_one_download(configured, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import time

    store, calls = configured
    original = store._download_stream

    def slow_fetch(url, path):
        time.sleep(0.12)
        return original(url, path)

    monkeypatch.setattr(store, "_download_stream", slow_fetch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(store.search, "json.loads", "Python_3") for _ in range(2)]
        outputs = [f.result(timeout=10) for f in futures]
    assert all(result[0]["symbol"] == "json.loads" for result in outputs)
    assert len(calls) == 1


def test_orphan_docset_directory_recovers_without_download(configured):
    store, calls = configured
    assert store.download_one("Python_3")["status"] == "installed"
    with store.db() as db:
        db.execute("DELETE FROM installed WHERE name='Python_3'")
        db.execute("DELETE FROM symbols")
    found = store.search("json.loads", "Python_3")
    assert found and found[0]["symbol"] == "json.loads"
    assert len(calls) == 1


def test_docker_automatic_defaults_are_declared():
    compose = (ROOT / "compose.yaml").read_text()
    assert 'DOCSET_AUTO_INSTALL: ${DOCSET_AUTO_INSTALL:-missing}' in compose
    assert 'DOCSET_AUTO_INDEX_CONTENT: ${DOCSET_AUTO_INDEX_CONTENT:-true}' in compose
    assert 'DOCSET_REFRESH_CATALOG_PERIODICALLY: ${DOCSET_REFRESH_CATALOG_PERIODICALLY:-true}' in compose
    assert 'DOCSET_CATALOG_REFRESH_HOURS: ${DOCSET_CATALOG_REFRESH_HOURS:-24}' in compose
    assert "AUTOMATION.md" in (ROOT / "README.md").read_text()


def test_tool_handler_is_async_so_download_does_not_block_mcp(monkeypatch, tmp_path):
    import asyncio
    import types

    from test_docsets import make_archive

    monkeypatch.setenv("DOCSET_AUTO_INSTALL", "missing")
    store = docsets.DocsetStore(tmp_path / "data")
    store.catalog_file.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(),
        "complete_catalog": True, "entries": [{"id": "com.kapeli.dash:Python_3",
        "name": "Python_3", "source": "com.kapeli.dash", "versions": [], "latest": "unknown"}]}))
    archive = tmp_path / "python.tgz"
    make_archive(archive)
    monkeypatch.setattr(store, "_download_stream", lambda url, part: part.write_bytes(archive.read_bytes()))

    class FakeMCP:
        def __init__(self, *args, **kwargs):
            self.tools = {}
        def tool(self):
            def wrapper(func):
                self.tools[func.__name__] = func
                return func
            return wrapper
        def prompt(self):
            return lambda f: f
        def run(self, **kwargs):
            assert asyncio.iscoroutinefunction(self.tools["search_docs"])
            found = asyncio.run(self.tools["search_docs"]("json.loads", "Python_3"))
            assert found[0]["symbol"] == "json.loads"

    fake_mcp = types.ModuleType("mcp")
    fake_server = types.ModuleType("mcp.server")
    fake_server.MCPServer = FakeMCP
    monkeypatch.setitem(sys.modules, "mcp", fake_mcp)
    monkeypatch.setitem(sys.modules, "mcp.server", fake_server)
    monkeypatch.setattr(mcp_server, "DocsetStore", lambda: store)
    mcp_server.run_server("stdio")

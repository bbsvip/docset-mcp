"""Offline fixtures for the core downloader/indexer/search path."""
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))
import docsets


def make_archive(path: Path):
    with tarfile.open(path, "w:gz") as out:
        payload = io.BytesIO()
        with sqlite3.connect(":memory:") as db:
            # SQLite in-memory dump can't simply be serialized; build an on-disk fixture.
            pass
        idx = path.parent / "test.dsidx"
        with sqlite3.connect(idx) as db:
            db.execute("CREATE TABLE searchIndex(id INTEGER PRIMARY KEY, name TEXT, type TEXT, path TEXT)")
            db.execute("INSERT INTO searchIndex(name,type,path) VALUES ('json.loads','Function','library/json.html#json.loads')")
            db.execute("INSERT INTO searchIndex(name,type,path) VALUES ('json.dumps','Function','library/json.html#json.dumps')")
            db.commit()
        for target, contents in (
                ("Python_3.docset/Contents/Resources/docSet.dsidx", idx.read_bytes()),
                ("Python_3.docset/Contents/Resources/Documents/library/json.html", 
                 b'<html><body><main><h1>json module</h1><h2 id="json.loads">json.loads</h2><p>Deserialize JSON into Python data.</p><h2 id="json.dumps">json.dumps</h2><p>Serialize Python data.</p></main></body></html>')):
            info = tarfile.TarInfo(target)
            info.size = len(contents)
            out.addfile(info, io.BytesIO(contents))
    idx.unlink()


@pytest.fixture
def store(tmp_path):
    docs = docsets.DocsetStore(tmp_path / "data")
    docs.catalog_file.write_text(json.dumps({
        "fetched_at": "2026-11-02T00:00:00Z", "complete_catalog": True,
        "entries": [{"id": "com.kapeli.dash:Python_3", "name": "Python_3", "source": "com.kapeli.dash",
                     "versions": ["3.14.0"], "latest": "3.14.0"}]
    }), encoding="utf-8")
    return docs


def test_catalog_normalization():
    c = docsets.DocsetStore.normalize_catalog([
        {"name": "Python_3", "sourceId": "com.kapeli.dash", "versions": ["3.14"]},
        {"name": "Python_3", "sourceId": "com.kapeli.dash", "versions": ["3.14"]},
        {"name": "../../evil", "sourceId": "com.kapeli.contrib"},
        {"name": "Cheetah_Cheatsheet", "sourceId": "com.kapeli.cheatsheet"}])
    assert len(c) == 2
    assert c[0]["id"].startswith("com.kapeli")


def test_install_search_read_resume(store, tmp_path, monkeypatch):
    tarpath = tmp_path / "python.tgz"
    make_archive(tarpath)
    monkeypatch.setattr(store, "_download_stream", lambda url, part: (part.write_bytes(tarpath.read_bytes()) or 1))
    res = store.download_one("Python_3")
    assert res["status"] == "installed"
    assert res["symbols"] == 2
    assert store.download_one("Python_3")["status"] == "skipped"
    items = store.search("loads", docset="Python_3")
    assert len(items) == 1
    assert "json.loads" in items[0]["symbol"]
    content = store.read_ref(items[0]["ref"])
    assert "Deserialize JSON" in content["content"]
    assert "Serialize Python" not in content["content"]
    assert store.download_all(dry_run=True)["pending"] == 0


def test_reject_traversal(store, tmp_path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as out:
        f = tarfile.TarInfo("../../escaped.txt")
        f.size = 3
        out.addfile(f, io.BytesIO(b"bad"))
    with pytest.raises(ValueError, match="Unsafe archive"):
        store._extract_archive(archive, tmp_path / "extracted")
    assert not (tmp_path / "escaped.txt").exists()


def test_reject_symlink(store, tmp_path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as out:
        item = tarfile.TarInfo("fake.docset/link")
        item.type = tarfile.SYMTYPE
        item.linkname = "../../secret"
        out.addfile(item)
    with pytest.raises(ValueError, match="Symlinks"):
        store._extract_archive(archive, tmp_path / "extracted")


def test_read_disallows_parent_traversal(store, tmp_path, monkeypatch):
    archive = tmp_path / "python.tgz"
    make_archive(archive)
    monkeypatch.setattr(store, "_download_stream", lambda u, p: (p.write_bytes(archive.read_bytes()) or 1))
    store.download_one("Python_3")
    with pytest.raises(ValueError, match="Unsafe documentation path"):
        store.read("Python_3", "../../etc/passwd")


def test_sqlite_reindex_atomic(store, tmp_path, monkeypatch):
    archive = tmp_path / "python.tgz"
    make_archive(archive)
    monkeypatch.setattr(store, "_download_stream", lambda u, p: (p.write_bytes(archive.read_bytes()) or 1))
    store.download_one("Python_3")
    before = len(store.search("json."))
    monkeypatch.setattr(store, "_read_rows", lambda p: (_ for _ in ()).throw(ValueError("bad index")))
    with pytest.raises(ValueError, match="bad index"):
        store.reindex_one("com.kapeli.dash:Python_3")
    assert len(store.search("json.")) == before


def test_bulk_dry_run_does_not_download(store, monkeypatch):
    monkeypatch.setattr(store, "download_one", lambda *a, **k: pytest.fail("download must not run"))
    res = store.download_all(dry_run=True)
    assert res["pending"] == 1 and res["complete_catalog"] is True


def test_zip_symlink_rejected(store, tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as out:
        info = zipfile.ZipInfo("foo.docset/linked")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        out.writestr(info, "../../target")
    with pytest.raises(ValueError, match="symlinks"):
        store._extract_archive(archive, tmp_path / "unzipped")


def test_fts_search(store, tmp_path, monkeypatch):
    archive = tmp_path / "python.tgz"
    make_archive(archive)
    monkeypatch.setattr(store, "_download_stream", lambda u, p: p.write_bytes(archive.read_bytes()))
    store.download_one("Python_3")
    info = store.index_content("Python_3")
    assert info["pages"] == 1
    results = store.search_content("Deserialize JSON", "Python_3")
    assert len(results) == 1
    assert "library/json.html" in results[0]["ref"]


def test_fts_missing_index(store):
    with pytest.raises(ValueError):
        store.search_content("query", "Python_3")


def test_archive_lock_prevents_parallel_writer(store):
    lock = store.home / "locks" / "test.lock"
    with docsets.process_file_lock(lock):
        with pytest.raises(RuntimeError, match="Another process"):
            with docsets.process_file_lock(lock):
                pytest.fail("Should not enter nested second lock")

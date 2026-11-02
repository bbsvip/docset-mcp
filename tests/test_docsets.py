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


def make_coredata_archive(path: Path, *, type_position="token", alternate_relation=False,
                          with_type_table=True, with_anchor=True):
    """Mimic a real Xcode/Core Data docset, not just a legacy Dash searchIndex."""
    idx = path.parent / ("coredata_" + type_position + ".dsidx")
    with sqlite3.connect(idx) as db:
        token_type = ", ZTOKENTYPE INTEGER" if type_position == "token" else ""
        meta_type = ", ZTOKENTYPE INTEGER" if type_position == "meta" else ""
        meta_link = ", ZTOKEN INTEGER" if alternate_relation else ""
        token_link = "" if alternate_relation else ", ZMETAINFORMATION INTEGER"
        anchor = ", ZANCHOR TEXT" if with_anchor else ""
        db.execute(f"CREATE TABLE ZTOKEN (Z_PK INTEGER PRIMARY KEY, ZTOKENNAME TEXT{token_link}{token_type})")
        db.execute(f"CREATE TABLE ZTOKENMETAINFORMATION (Z_PK INTEGER PRIMARY KEY, ZFILE INTEGER{anchor}{meta_link}{meta_type})")
        db.execute("CREATE TABLE ZFILEPATH (Z_PK INTEGER PRIMARY KEY, ZPATH TEXT)")
        if with_type_table:
            db.execute("CREATE TABLE ZTOKENTYPE (Z_PK INTEGER PRIMARY KEY, ZTYPENAME TEXT)")
            db.execute("INSERT INTO ZTOKENTYPE VALUES (1, 'func')")
        vals = [1, "json.loads"]
        if not alternate_relation:
            vals.append(1)
        if type_position == "token":
            vals.append(1)
        db.execute("INSERT INTO ZTOKEN VALUES (" + ",".join("?" * len(vals)) + ")", vals)
        vals = [1, 1]
        if with_anchor:
            vals.append("json.loads")
        if alternate_relation:
            vals.append(1)
        if type_position == "meta":
            vals.append(1)
        db.execute("INSERT INTO ZTOKENMETAINFORMATION VALUES (" + ",".join("?" * len(vals)) + ")", vals)
        db.execute("INSERT INTO ZFILEPATH VALUES (1, 'library/json.html')")
        db.commit()
    with tarfile.open(path, "w:gz") as out:
        for target, contents in (
                ("Python_3.docset/Contents/Resources/docSet.dsidx", idx.read_bytes()),
                ("Python_3.docset/Contents/Resources/Documents/library/json.html",
                 b'<html><body><main><h1>json module</h1><h2 id="json.loads">json.loads</h2><p>Deserialize JSON into Python data.</p><h2 id="json.dumps">json.dumps</h2><p>Serialize Python data.</p></main></body></html>')):
            info = tarfile.TarInfo(target)
            info.size = len(contents)
            out.addfile(info, io.BytesIO(contents))
    idx.unlink()


@pytest.mark.parametrize("type_position,alternate_relation,with_type_table,with_anchor", [
    ("token", False, True, True),  # Real-world Zeal Core Data layout; previously crashed.
    ("meta", False, True, True),   # Optional backward-compatible layout.
    ("token", True, True, True),   # Alternate relationship used by older docset generators.
    ("none", False, False, False), # Older index lacks type and anchor metadata.
])
def test_coredata_index_download_search_and_read(store, tmp_path, monkeypatch,
                                                  type_position, alternate_relation, with_type_table, with_anchor):
    archive = tmp_path / "coredata.tar.gz"
    make_coredata_archive(archive, type_position=type_position,
                          alternate_relation=alternate_relation,
                          with_type_table=with_type_table, with_anchor=with_anchor)
    monkeypatch.setattr(store, "_download_stream", lambda u, p: p.write_bytes(archive.read_bytes()))
    result = store.download_one("Python_3")
    assert result["status"] == "installed" and result["symbols"] == 1
    found = store.search("json.loads", "Python_3")
    assert len(found) == 1
    assert found[0]["symbol"] == "json.loads"
    if with_anchor:
        assert found[0]["ref"].endswith("library/json.html#json.loads")
        assert "Serialize Python" not in store.read_ref(found[0]["ref"])["content"]
    else:
        assert found[0]["ref"].endswith("library/json.html")
    assert found[0]["type"] == ("func" if with_type_table else "")


def test_repair_existing_docset_without_redownload(store, tmp_path, monkeypatch):
    archive = tmp_path / "coredata.tar.gz"
    make_coredata_archive(archive)
    monkeypatch.setattr(store, "_download_stream", lambda u, p: p.write_bytes(archive.read_bytes()))
    original = store._read_rows
    monkeypatch.setattr(store, "_read_rows", lambda _: (_ for _ in ()).throw(sqlite3.OperationalError("bad schema")))
    result = store.download_one("Python_3")
    assert "warning" in result and result["symbols"] == 0
    assert store.search("json.loads", "Python_3") == []
    monkeypatch.setattr(store, "_read_rows", original)
    # --failed-only reuses existing archive bytes on disk and rebuilds only the metadata DB.
    repaired = store.reindex_all(failed_only=True)
    assert repaired["indexed"] == 1 and repaired["failed"] == 0
    assert store.search("json.loads", "Python_3")[0]["symbol"] == "json.loads"
    assert store.reindex_all(failed_only=True)["selected"] == 0
    assert store.reindex_one("Python_3")["symbols"] == 1

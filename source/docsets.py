"""Standalone Dash/Zeal docset catalog, downloader, and offline search.
No Dash/Zeal installation, Python package installation, or project package layout required.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
import zipfile

from bs4 import BeautifulSoup

CATALOG_FULL = "https://api.zealdocs.org/_api/v1/catalog.json"
CATALOG_LEGACY = "https://api.zealdocs.org/v1/docsets"
URL_PREFIX = "https://go.zealdocs.org/d/"
AGENT = "DocsetAgentMCP/0.1 (+offline programming documentation client)"
_ALLOWED_SOURCE = {"com.kapeli", "com.kapeli.dash", "com.kapeli.contrib", "com.kapeli.cheatsheet"}
_SAFE = re.compile(r"^[A-Za-z0-9_.()+-]+$")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "true" if default else "false").strip().lower()
    if value not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
        raise ValueError(f"{name} must be true or false")
    return value in {"true", "1", "yes", "on"}


def env_positive_int(name: str, default: int, maximum: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def root_dir() -> Path:
    custom = os.environ.get("DOCSET_HOME")
    if custom:
        return Path(custom).expanduser().resolve()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DocsetAgentMCP"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "docset-agent-mcp"


def network_open(url: str, headers: dict | None = None, attempts: int = 4):
    if urlsplit(url).scheme != "https":
        raise ValueError("Only HTTPS catalog/download endpoints are accepted")
    last = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": AGENT, **(headers or {})})
            response = urlopen(request, timeout=45)
            if urlsplit(response.geturl()).scheme != "https":
                response.close()
                raise ValueError("Unsafe HTTP redirect")
            return response
        except HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
            retry_after = e.headers.get("Retry-After", "")
            delay = int(retry_after) if retry_after.isdigit() else 2 ** attempt
            time.sleep(min(45, delay))
        except (URLError, TimeoutError) as e:
            last = e
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)
    raise last or RuntimeError("Download error")


@contextlib.contextmanager
def process_file_lock(path: Path, wait_seconds: float = 0):
    """Portable process lock, optionally waiting for a competing writer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Another process is accessing {path.name}") from exc
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def safe_identifier(value: str) -> str:
    if not value or not _SAFE.fullmatch(value) or value in (".", ".."):
        raise ValueError(f"Invalid docset identifier: {value!r}")
    return value


class DocsetStore:
    def __init__(self, home: Path | None = None):
        self.home = Path(home or root_dir()).resolve()
        self.docs = self.home / "docsets"
        self.docs.mkdir(parents=True, exist_ok=True)
        self.home.mkdir(parents=True, exist_ok=True)
        self.dbfile = self.home / "index.sqlite3"
        self.catalog_file = self.home / "catalog.json"
        self.lock = threading.RLock()
        self.auto_install = os.environ.get("DOCSET_AUTO_INSTALL", "off").strip().lower()
        if self.auto_install not in ("off", "missing"):
            raise ValueError("DOCSET_AUTO_INSTALL must be off or missing")
        self.auto_index_content = env_bool("DOCSET_AUTO_INDEX_CONTENT")
        self.refresh_catalog_periodically = env_bool("DOCSET_REFRESH_CATALOG_PERIODICALLY")
        self.catalog_refresh_hours = env_positive_int("DOCSET_CATALOG_REFRESH_HOURS", 24, 24 * 365)
        self.fts_max_pages = env_positive_int("DOCSET_FTS_MAX_PAGES", 100000, 100000)
        self.fts_backfill_on_start = env_bool("DOCSET_FTS_BACKFILL_ON_START", True)
        self._init_db()
        self._recover_backups()

    @contextlib.contextmanager
    def db(self):
        conn = sqlite3.connect(self.dbfile, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=60000")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS installed (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, name TEXT NOT NULL,
                    version TEXT, installed_at TEXT NOT NULL, symbols INTEGER NOT NULL DEFAULT 0,
                    indexed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS symbols (
                    docset_id TEXT NOT NULL, name TEXT NOT NULL, kind TEXT,
                    path TEXT NOT NULL, name_lc TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_symbols_docset ON symbols(docset_id);
                CREATE INDEX IF NOT EXISTS ix_symbols_name ON symbols(name_lc);
                CREATE INDEX IF NOT EXISTS ix_symbols_doc_name ON symbols(docset_id, name_lc);
            """)

    def _recover_backups(self):
        # If a previous process was interrupted between swapping docset dirs, keep the old copy.
        for backup in self.docs.glob("*.docset.backup"):
            target = backup.with_name(backup.name[:-len(".backup")])
            if not target.exists():
                backup.replace(target)

    @staticmethod
    def normalize_catalog(data):
        # Zeal publishes a full catalog (Dash, contrib, cheatsheets) and a smaller legacy fallback.
        if isinstance(data, dict):
            if isinstance(data.get("docsets"), list):
                data = data["docsets"]
            else:
                raise ValueError("Unexpected catalog object")
        if not isinstance(data, list):
            raise ValueError("Unexpected catalog payload")
        result = []
        seen = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            name, source = item.get("name", ""), item.get("sourceId", "com.kapeli")
            if not isinstance(name, str) or not isinstance(source, str):
                continue
            if source not in _ALLOWED_SOURCE:
                continue
            try:
                safe_identifier(name)
            except ValueError:
                continue
            key = f"{source}:{name}"
            if key in seen:
                continue
            versions = item.get("versions") or []
            if not isinstance(versions, list):
                versions = []
            result.append({"id": key, "name": name, "source": source,
                           "versions": [str(x) for x in versions if isinstance(x, (str, int, float))],
                           "latest": str(versions[0]) if versions else "unknown"})
            seen.add(key)
        return sorted(result, key=lambda x: (x["name"].casefold(), x["source"]))

    def sync_catalog(self) -> dict:
        """Serialize catalog writes across containers and make the JSON swap atomic."""
        with process_file_lock(self.home / "locks" / "catalog.lock", wait_seconds=90):
            return self._sync_catalog_unlocked()

    def _sync_catalog_unlocked(self) -> dict:
        errors = []
        for url in (CATALOG_FULL, CATALOG_LEGACY):
            try:
                with network_open(url) as resp:
                    raw = resp.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise ValueError("Catalog too large")
                entries = self.normalize_catalog(json.loads(raw))
                if not entries:
                    raise ValueError("Empty catalog")
                payload = {"fetched_at": timestamp(), "source_url": url,
                           "complete_catalog": url == CATALOG_FULL, "entries": entries}
                tmp = self.home / "catalog.json.tmp"
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, self.catalog_file)
                return {"count": len(entries), "source": url, "complete": url == CATALOG_FULL}
            except (OSError, URLError, HTTPError, ValueError, json.JSONDecodeError) as e:
                errors.append(f"{url}: {e}")
        raise RuntimeError("Cannot fetch catalog: " + " | ".join(errors))

    def catalog_refresh_due(self) -> bool:
        if not self.catalog_file.is_file():
            return True
        try:
            cached = json.loads(self.catalog_file.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(cached["fetched_at"].replace("Z", "+00:00"))
            if fetched.tzinfo is None:
                return True
            age = (datetime.now(timezone.utc) - fetched).total_seconds()
            return age >= self.catalog_refresh_hours * 3600
        except (OSError, ValueError, KeyError, TypeError):
            return True

    def refresh_catalog_if_due(self) -> dict:
        """Refresh only when stale. Preserve an older catalog on network failures."""
        if not self.catalog_refresh_due():
            return {"status": "fresh"}
        try:
            with process_file_lock(self.home / "locks" / "catalog.lock", wait_seconds=90):
                if not self.catalog_refresh_due():
                    return {"status": "fresh"}
                return {"status": "updated", **self._sync_catalog_unlocked()}
        except RuntimeError as exc:
            # Offline usage must keep working with the last known catalog.
            return {"status": "failed", "error": str(exc),
                    "cached": self.catalog_file.is_file()}

    def catalog(self, refresh: bool = False) -> dict:
        if refresh or not self.catalog_file.exists():
            self.sync_catalog()
        return json.loads(self.catalog_file.read_text(encoding="utf-8"))

    def catalog_search(self, query: str = "", limit: int = 30, refresh: bool = False) -> dict:
        payload = self.catalog(refresh)
        items = payload["entries"]
        if query:
            q = query.casefold()
            items = [x for x in items if q in x["name"].casefold() or q in x["source"].casefold()]
        return {"total": len(items), "items": items[:max(1, min(limit, 1000))],
                "catalog_count": len(payload["entries"]), "complete_catalog": payload["complete_catalog"],
                "fetched_at": payload["fetched_at"]}

    def _resolve_catalog_entry(self, name: str, source: str | None = None) -> dict:
        entries = self.catalog()["entries"]
        if ":" in name and source is None:
            source, name = name.split(":", 1)
        hits = [e for e in entries if e["name"].casefold() == name.casefold() and (source is None or e["source"] == source)]
        if not hits:
            raise ValueError(f"Docset {name!r} not in catalog")
        if len(hits) > 1:
            raise ValueError("Ambiguous docset; specify one of: " + ", ".join(x["id"] for x in hits))
        return hits[0]

    def path_for(self, identifier: str):
        # Use a normalized filename, not archive-controlled docset names.
        source, name = identifier.split(":", 1)
        if source not in _ALLOWED_SOURCE:
            raise ValueError("Invalid source")
        safe_identifier(name)
        return self.docs / (source.replace(".", "_") + "__" + name + ".docset")

    def installed(self, limit: int = 1000) -> list[dict]:
        with self.db() as db:
            rows = db.execute("SELECT * FROM installed ORDER BY name LIMIT ?", (max(1, min(limit, 2000)),)).fetchall()
        return [dict(row) for row in rows if self.path_for(row["id"]).is_dir()]

    def ensure_docset(self, docset: str) -> dict:
        """On-demand install only for an explicit, unambiguous docset identifier.

        Never infer a docset from free-text search or download the whole catalog.
        """
        with self.db() as db:
            matches = db.execute("SELECT id, indexed FROM installed WHERE id=? OR name=? COLLATE NOCASE",
                                 (docset, docset)).fetchall()
        if len(matches) > 1:
            raise ValueError("Ambiguous docset; use source:name: " + docset)
        if matches and self.path_for(matches[0]["id"]).is_dir():
            if not matches[0]["indexed"]:
                self.reindex_one(matches[0]["id"])
            return {"status": "already_installed", "id": matches[0]["id"]}
        if self.auto_install != "missing":
            raise ValueError("Docset not installed: " + docset + " (auto-install disabled)")
        # download_one checks catalog identity, acquires a per-docset process lock,
        # and safely skips if another client completed the installation first.
        result = self.download_one(docset)
        if result.get("warning"):
            raise RuntimeError(result["warning"])
        if result.get("status") == "skipped":
            return self.ensure_docset(docset)
        return result

    def _choose_archive(self, stage: Path):
        folders = [p for p in stage.rglob("*.docset") if p.is_dir()]
        valid = [x for x in folders if (x / "Contents" / "Resources" / "docSet.dsidx").is_file()]
        if len(valid) != 1:
            raise ValueError("Archive must contain exactly one valid .docset/Contents/Resources/docSet.dsidx")
        return valid[0]

    @staticmethod
    def _check_member(name: str, size: int, expanded: list[int]):
        p = PurePosixPath(name.replace("\\", "/"))
        if not name or p.is_absolute() or ".." in p.parts or not p.parts or ":" in p.parts[0]:
            raise ValueError("Unsafe archive member path")
        expanded[0] += size
        if expanded[0] > int(os.environ.get("DOCSET_MAX_EXPANDED_GB", "12")) * 1024 ** 3:
            raise ValueError("Expanded archive exceeds DOCSET_MAX_EXPANDED_GB")
        return p

    def _extract_archive(self, archive: Path, dest: Path):
        # Never call extractall(): reject traversal and links and bound expanded size.
        expanded = [0]
        count = 0
        max_files = int(os.environ.get("DOCSET_MAX_FILES", "250000"))
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as z:
                for info in z.infolist():
                    count += 1
                    if count > max_files:
                        raise ValueError("Archive contains too many files")
                    p = self._check_member(info.filename, info.file_size, expanded)
                    mode = (info.external_attr >> 16) & 0o170000
                    if mode == 0o120000:
                        raise ValueError("Zip symlinks are disallowed")
                    target = dest.joinpath(*p.parts)
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    if mode not in (0, 0o100000):
                        raise ValueError("Unsupported zip member type")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, target.open("wb") as out:
                        shutil.copyfileobj(src, out, length=1024 * 1024)
        else:
            with tarfile.open(archive, "r:*") as t:
                for info in t:
                    count += 1
                    if count > max_files:
                        raise ValueError("Archive contains too many files")
                    p = self._check_member(info.name, info.size if info.isfile() else 0, expanded)
                    target = dest.joinpath(*p.parts)
                    if info.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif info.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        src = t.extractfile(info)
                        if src is None:
                            raise ValueError("Invalid tar entry")
                        with src, target.open("wb") as out:
                            shutil.copyfileobj(src, out, length=1024 * 1024)
                    else:
                        raise ValueError("Symlinks, hardlinks and special files are disallowed")

    @staticmethod
    def _read_rows(docset_path: Path):
        """Read Dash searchIndex or Xcode/Core Data indices without mutating the docset.

        The Core Data schema stores the type FK on ZTOKEN (not on
        ZTOKENMETAINFORMATION). Other generations omit some optional columns,
        so detect them rather than assuming one fixed layout.
        """
        idx = docset_path / "Contents" / "Resources" / "docSet.dsidx"
        uri = idx.as_uri() + "?mode=ro&immutable=1"
        with sqlite3.connect(uri, uri=True, timeout=10) as db:
            tables = {str(row[0]).lower() for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}

            def columns(table: str) -> set[str]:
                # The table names below are hard-coded, never user-provided.
                return {str(row[1]).lower() for row in db.execute(f"PRAGMA table_info({table})")}

            def combine(path, fragment):
                if path is None:
                    return path
                path = str(path)
                if fragment and "#" not in path:
                    path += "#" + str(fragment).lstrip("#")
                return path

            if "searchindex" in tables:
                fields = columns("searchIndex")
                if not {"name", "path"}.issubset(fields):
                    raise ValueError("Invalid searchIndex: missing name or path column")
                type_col = "type" if "type" in fields else "NULL"
                frag_col = "fragment" if "fragment" in fields else "NULL"
                for name, kind, path, fragment in db.execute(
                    f"SELECT name, {type_col}, path, {frag_col} FROM searchIndex"):
                    yield name, kind, combine(path, fragment)
                return

            if not {"ztoken", "ztokenmetainformation", "zfilepath"}.issubset(tables):
                raise ValueError(f"Unsupported docSet.dsidx schema: {sorted(tables)[:12]}")

            token = columns("ZTOKEN")
            meta = columns("ZTOKENMETAINFORMATION")
            files = columns("ZFILEPATH")
            if "ztokenname" not in token or "zpath" not in files:
                raise ValueError("Core Data index lacks ZTOKENNAME or ZPATH")
            if "zmetainformation" in token:
                meta_join = "t.ZMETAINFORMATION = m.Z_PK"
            elif "ztoken" in meta:
                meta_join = "m.ZTOKEN = t.Z_PK"
            else:
                raise ValueError("Unsupported Core Data token-to-metadata relationship")
            if "zfile" in meta:
                file_join = "m.ZFILE = f.Z_PK"
            elif "zfile" in token:
                file_join = "t.ZFILE = f.Z_PK"
            else:
                raise ValueError("Unsupported Core Data metadata-to-file relationship")

            type_fk = ("t.ZTOKENTYPE" if "ztokentype" in token else
                       "m.ZTOKENTYPE" if "ztokentype" in meta else None)
            if type_fk and "ztokentype" in tables and {"z_pk", "ztypename"}.issubset(columns("ZTOKENTYPE")):
                type_join = f"LEFT JOIN ZTOKENTYPE tt ON {type_fk} = tt.Z_PK"
                type_expr = "tt.ZTYPENAME"
            else:
                type_join = ""
                type_expr = "NULL"
            anchor_expr = "m.ZANCHOR" if "zanchor" in meta else "NULL"
            sql = f"""SELECT t.ZTOKENNAME, {type_expr}, f.ZPATH, {anchor_expr}
                      FROM ZTOKEN t
                      JOIN ZTOKENMETAINFORMATION m ON {meta_join}
                      JOIN ZFILEPATH f ON {file_join}
                      {type_join}"""
            for name, kind, path, fragment in db.execute(sql):
                yield name, kind, combine(path, fragment)

    def _installed_id(self, docset: str) -> str:
        with self.db() as db:
            keys = db.execute(
                "SELECT id FROM installed WHERE id=? OR name=? COLLATE NOCASE",
                (docset, docset)).fetchall()
        if len(keys) != 1:
            raise ValueError("Docset not installed or ambiguous: " + docset)
        return keys[0]["id"]

    def reindex_one(self, docset_id: str) -> dict:
        docset_id = self._installed_id(docset_id)
        location = self.path_for(docset_id)
        if not location.is_dir():
            raise ValueError("Docset is not installed")
        count = 0
        with self.lock, self.db() as db:
            # Atomic DB transaction: old index remains if insertion fails.
            db.execute("DELETE FROM symbols WHERE docset_id=?", (docset_id,))
            batch = []
            for name, kind, path in self._read_rows(location):
                if not name or not path:
                    continue
                batch.append((docset_id, str(name), str(kind or ""), str(path), str(name).casefold()))
                if len(batch) >= 2000:
                    db.executemany("INSERT INTO symbols VALUES (?,?,?,?,?)", batch)
                    count += len(batch)
                    batch.clear()
            if batch:
                db.executemany("INSERT INTO symbols VALUES (?,?,?,?,?)", batch)
                count += len(batch)
            db.execute("UPDATE installed SET indexed=1, symbols=? WHERE id=?", (count, docset_id))
        return {"id": docset_id, "symbols": count}

    def reindex_all(self, failed_only: bool = False, log=None) -> dict:
        """Repair installed docsets, including downloads where index creation failed."""
        with self.db() as db:
            query = "SELECT id FROM installed" + (" WHERE indexed=0" if failed_only else "")
            keys = [row["id"] for row in db.execute(query + " ORDER BY name")]
        success = 0
        failures = []
        for key in keys:
            try:
                result = self.reindex_one(key)
                success += 1
                event = {"status": "indexed", **result}
            except (sqlite3.Error, ValueError, OSError) as exc:
                event = {"status": "failed", "id": key, "error": str(exc)}
                failures.append(event)
            if log:
                log(event)
        return {"selected": len(keys), "indexed": success, "failed": len(failures),
                "failures": failures[:100]}

    def _download_stream(self, url: str, part: Path):
        size = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={size}-"} if size else {}
        try:
            resp = network_open(url, headers=headers)
        except HTTPError as e:
            if e.code != 416:
                raise
            part.unlink(missing_ok=True)
            size = 0
            resp = network_open(url)
        with resp:
            if resp.status == 206 and size:
                content_range = resp.headers.get("Content-Range", "")
                if not content_range.startswith(f"bytes {size}-"):
                    raise ValueError("Unexpected Content-Range: " + content_range)
                mode = "ab"
            else:
                size = 0
                mode = "wb"
            upper = int(os.environ.get("DOCSET_MAX_ARCHIVE_GB", "4")) * 1024 ** 3
            with part.open(mode) as fd:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > upper:
                        raise ValueError("Archive exceeds DOCSET_MAX_ARCHIVE_GB")
                    fd.write(chunk)
        return size

    def download_one(self, name: str, source: str | None = None, update: bool = False) -> dict:
        entry = self._resolve_catalog_entry(name, source)
        with process_file_lock(self.home / "locks" / (entry["id"].replace(":", "__") + ".lock"), wait_seconds=3600):
            return self._download_one_locked(entry, update)

    def _download_one_locked(self, entry: dict, update: bool = False) -> dict:
        key = entry["id"]
        target = self.path_for(key)
        if target.is_dir() and not update:
            # Recover a directory moved into place just before a process crash,
            # when its installed-row transaction never completed.
            with self.db() as db:
                exists = db.execute("SELECT indexed FROM installed WHERE id=?", (key,)).fetchone()
                if exists is None:
                    db.execute("INSERT INTO installed (id,source,name,version,installed_at,indexed) "
                               "VALUES (?,?,?,?,?,0)",
                               (key, entry["source"], entry["name"], entry["latest"], timestamp()))
            if exists is None:
                result = self.reindex_one(key)
                if self.auto_index_content:
                    result["content_index"] = self.index_content(key, self.fts_max_pages,
                                                                 only_if_missing=True)
                return {"id": key, "status": "recovered", **result}
            return {"id": key, "status": "skipped", "reason": "already installed"}
        archive_dir = self.home / "downloads"
        archive_dir.mkdir(parents=True, exist_ok=True)
        # Include advertised version so a stale .part is not appended to a newer release.
        version_token = re.sub(r"[^A-Za-z0-9_.+-]", "_", entry["latest"])[:80]
        part = archive_dir / (target.name + "__" + version_token + ".part")
        url = URL_PREFIX + "/".join(quote(x, safe="") for x in (entry["source"], entry["name"], "latest"))
        bytes_ = self._download_stream(url, part)
        stage = Path(tempfile.mkdtemp(prefix="extract_", dir=self.home))
        backup = target.with_name(target.name + ".backup")
        try:
            self._extract_archive(part, stage)
            extracted = self._choose_archive(stage)
            # Verify index is well-formed before touching an old installation.
            with sqlite3.connect((extracted / "Contents" / "Resources" / "docSet.dsidx").as_uri() + "?mode=ro", uri=True) as probe:
                probe.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            with self.lock:
                if backup.exists():
                    shutil.rmtree(backup)
                if target.exists():
                    target.replace(backup)
                try:
                    extracted.replace(target)
                    with self.db() as db:
                        db.execute("INSERT INTO installed (id,source,name,version,installed_at, indexed) VALUES (?,?,?,?,?,0) "
                                   "ON CONFLICT(id) DO UPDATE SET version=excluded.version, installed_at=excluded.installed_at, indexed=0",
                                   (key, entry["source"], entry["name"], entry["latest"], timestamp()))
                    try:
                        index = self.reindex_one(key)
                    except (sqlite3.Error, ValueError, OSError) as e:
                        index = {"symbols": 0, "warning": f"Docset installed but index unavailable: {e}"}
                    if backup.exists():
                        shutil.rmtree(backup)
                    # Full-text index, if present, refers to the old file contents.
                    self._fts_path(key).unlink(missing_ok=True)
                except BaseException:
                    if target.exists():
                        shutil.rmtree(target)
                    if backup.exists():
                        backup.replace(target)
                    raise
            part.unlink(missing_ok=True)
            result = {"id": key, "status": "installed", "bytes": bytes_, **index}
            if self.auto_index_content:
                try:
                    result["content_index"] = self.index_content(key, self.fts_max_pages,
                                                                 only_if_missing=True)
                except (sqlite3.Error, ValueError, OSError, RuntimeError) as exc:
                    # Preserve the usable symbol index; FTS can be rebuilt later.
                    result["content_index_warning"] = str(exc)
            return result
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def download_all(self, workers: int = 2, delay: float = 2.0, max_items: int = 0,
                     update: bool = False, source: str = "", dry_run: bool = False,
                     log=None) -> dict:
        if dry_run:
            return self._download_all_locked(workers, delay, max_items, update, source, dry_run, log)
        with process_file_lock(self.home / "bulk_download.lock"):
            return self._download_all_locked(workers, delay, max_items, update, source, dry_run, log)

    def _download_all_locked(self, workers, delay, max_items, update, source, dry_run, log):
        catalog = self.catalog()
        entries = [x for x in catalog["entries"] if not source or x["source"] == source]
        if not update:
            entries = [x for x in entries if not self.path_for(x["id"]).is_dir()]
        total_pending = len(entries)
        if max_items > 0:
            entries = entries[:max_items]
        if dry_run:
            return {"catalog_count": len(catalog["entries"]), "pending": total_pending,
                    "selected": len(entries), "sample": [x["id"] for x in entries[:20]],
                    "complete_catalog": catalog["complete_catalog"]}
        if workers < 1 or workers > 3:
            raise ValueError("workers must be 1..3 to avoid overloading upstream servers")
        if delay < 0:
            raise ValueError("delay must be nonnegative")
        counts = {"installed": 0, "recovered": 0, "skipped": 0, "failed": 0}
        failures = []
        jobs_lock = threading.Lock()
        next_slot = [time.monotonic()]
        def job(entry):
            with jobs_lock:
                wait = max(0.0, next_slot[0] - time.monotonic())
                next_slot[0] = max(time.monotonic(), next_slot[0]) + delay
            if wait:
                time.sleep(wait)
            try:
                return self.download_one(entry["name"], entry["source"], update=update)
            except Exception as exc:
                return {"id": entry["id"], "status": "failed", "error": str(exc)}
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for item in pool.map(job, entries):
                counts[item["status"]] += 1
                if item["status"] == "failed":
                    failures.append(item)
                if log:
                    log(item)
        return {"selected": len(entries), "pending_at_start": total_pending,
                "catalog_count": len(catalog["entries"]), "complete_catalog": catalog["complete_catalog"],
                **counts, "failures": failures[:100]}

    def _fts_path(self, identifier: str) -> Path:
        # Identifiers contain only checked source/name characters.
        _ = self.path_for(identifier)
        folder = self.home / "fts"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / (identifier.replace(":", "__") + ".sqlite3")

    def index_content(self, docset: str, max_pages: int = 10000,
                      only_if_missing: bool = False) -> dict:
        """Build OPTIONAL full-text search for one installed docset.

        Separate SQLite DB avoids multiplying the size/cost of symbol-only lookups.
        """
        with self.db() as db:
            keys = db.execute("SELECT id FROM installed WHERE id=? OR name=? COLLATE NOCASE", (docset, docset)).fetchall()
        if len(keys) != 1:
            raise ValueError("Docset missing/ambiguous: " + docset)
        key = keys[0]["id"]
        root = self.path_for(key) / "Contents" / "Resources" / "Documents"
        if not root.is_dir():
            raise ValueError("No Documents directory")
        max_pages = max(1, min(int(max_pages), 100000))
        dbfile = self._fts_path(key)
        with process_file_lock(self.home / "locks" / (key.replace(":", "__") + ".fts.lock"), wait_seconds=3600):
            if only_if_missing and dbfile.is_file():
                return {"id": key, "status": "skipped", "fts_db": str(dbfile)}
            stage = dbfile.with_name(dbfile.name + ".tmp")
            stage.unlink(missing_ok=True)
            pages = 0
            try:
                with sqlite3.connect(stage) as db:
                    db.execute("CREATE VIRTUAL TABLE docs USING fts5(path UNINDEXED, title, body)")
                    batch = []
                    for file in root.rglob("*"):
                        if pages >= max_pages:
                            break
                        if file.suffix.lower() not in (".html", ".htm") or not file.is_file() or file.is_symlink():
                            continue
                        if file.stat().st_size > 3 * 1024 * 1024:
                            continue
                        try:
                            soup = BeautifulSoup(file.read_text(encoding="utf-8", errors="replace"), "html.parser")
                            title = soup.title.get_text(" ", strip=True) if soup.title else file.stem
                            for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
                                tag.decompose()
                            body = (soup.main or soup.article or soup.body or soup).get_text(" ", strip=True)
                            if not body:
                                continue
                            batch.append((file.relative_to(root).as_posix(), title[:350], body[:250000]))
                            pages += 1
                            if len(batch) >= 100:
                                db.executemany("INSERT INTO docs(path,title,body) VALUES(?,?,?)", batch)
                                batch.clear()
                        except (OSError, ValueError, UnicodeError):
                            continue
                    if batch:
                        db.executemany("INSERT INTO docs(path,title,body) VALUES(?,?,?)", batch)
                    db.commit()
                os.replace(stage, dbfile)
            finally:
                stage.unlink(missing_ok=True)
            return {"id": key, "pages": pages, "fts_db": str(dbfile), "status": "indexed",
                    "max_pages_reached": pages >= max_pages}

    def index_content_all(self, missing_only: bool = True, log=None) -> dict:
        """Backfill existing installed docsets without downloading them again."""
        keys = [row["id"] for row in self.installed(2000)]
        counts = {"indexed": 0, "skipped": 0, "failed": 0}
        errors = []
        for key in keys:
            try:
                result = self.index_content(key, self.fts_max_pages,
                                            only_if_missing=missing_only)
                counts[result["status"]] += 1
            except (sqlite3.Error, OSError, ValueError, RuntimeError) as exc:
                result = {"id": key, "status": "failed", "error": str(exc)}
                counts["failed"] += 1
                errors.append(result)
            if log:
                log(result)
        return {"selected": len(keys), **counts, "failures": errors[:100]}

    def search_content(self, query: str, docset: str, limit: int = 10) -> list[dict]:
        """Search text (not only symbols) in an explicitly indexed docset."""
        self.ensure_docset(docset)
        with self.db() as db:
            keys = db.execute("SELECT id FROM installed WHERE id=? OR name=? COLLATE NOCASE", (docset, docset)).fetchall()
        if len(keys) != 1:
            raise ValueError("Docset missing/ambiguous: " + docset)
        key = keys[0]["id"]
        dbfile = self._fts_path(key)
        if not dbfile.is_file():
            if self.auto_index_content:
                self.index_content(key, self.fts_max_pages, only_if_missing=True)
            else:
                raise ValueError("Full text not indexed. Run index-content for " + key)
        terms = re.findall(r"[\w]+", query, flags=re.UNICODE)[:12]
        if not terms:
            raise ValueError("No search terms")
        # Literal quoted terms prevent callers injecting FTS5 query syntax.
        match = " AND ".join('"' + t.replace('"', '') + '"' for t in terms)
        with sqlite3.connect(dbfile, timeout=20) as db:
            rows = db.execute("SELECT path,title,snippet(docs,2,'[',']','…',20) "
                              "FROM docs WHERE docs MATCH ? ORDER BY rank LIMIT ?",
                              (match, max(1,min(int(limit),50)))).fetchall()
        return [{"id": key, "path": r[0], "title": r[1], "snippet": r[2],
                 "ref": f"{key}::{r[0]}"} for r in rows]

    def search(self, query: str, docset: str = "", limit: int = 20) -> list[dict]:
        if not query.strip():
            raise ValueError("Search query is empty")
        q = query.casefold().strip()
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        # LIKE contains query for findability; exact/prefix matches ranked first.
        where = "WHERE name_lc LIKE ? ESCAPE '\\'"
        params = [f"%{escaped}%"]
        if docset:
            self.ensure_docset(docset)
            with self.db() as db:
                choices = db.execute("SELECT id FROM installed WHERE id=? OR name=? COLLATE NOCASE", (docset, docset)).fetchall()
            if not choices:
                raise ValueError("Docset not installed: " + docset)
            if len(choices) > 1:
                raise ValueError("Ambiguous docset; specify source:name")
            where += " AND docset_id=?"
            params.append(choices[0]["id"])
        sql = f"""SELECT docset_id, name, kind, path FROM symbols {where}
                  ORDER BY CASE WHEN name_lc = ? THEN 0 WHEN name_lc LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END,
                  length(name), docset_id LIMIT ?"""
        params.extend([q, escaped + "%", max(1, min(limit, 100))])
        with self.db() as db:
            # Indexed prefix matches avoid a full-table LIKE scan for common API queries.
            prefix_where = "WHERE name_lc >= ? AND name_lc < ?"
            prefix_params = [q, q + "\uffff"]
            if docset:
                prefix_where += " AND docset_id=?"
                prefix_params.append(choices[0]["id"])
            prefix_sql = ("SELECT docset_id,name,kind,path FROM symbols " + prefix_where +
                          " ORDER BY length(name), name_lc LIMIT ?")
            prefix = db.execute(prefix_sql, [*prefix_params, max(1, min(limit,100))]).fetchall()
            if len(prefix) >= max(1, min(limit,100)):
                rows = prefix
            else:
                rows = db.execute(sql, params).fetchall()
        return [{"id": r["docset_id"], "symbol": r["name"], "type": r["kind"],
                 "path": r["path"], "ref": f"{r['docset_id']}::{r['path']}"} for r in rows]

    def read(self, docset: str, path: str, max_chars: int = 12000) -> dict:
        max_chars = max(300, min(max_chars, 50000))
        self.ensure_docset(docset)
        with self.db() as db:
            rows = db.execute("SELECT id FROM installed WHERE id=? OR name=? COLLATE NOCASE", (docset, docset)).fetchall()
        if len(rows) != 1:
            raise ValueError("Docset not found or ambiguous: " + docset)
        key = rows[0]["id"]
        docroot = (self.path_for(key) / "Contents" / "Resources" / "Documents").resolve()
        path, _, anchor = path.partition("#")
        path = path.split("?", 1)[0].replace("\\", "/")
        rel = PurePosixPath(path)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts or not path:
            raise ValueError("Unsafe documentation path")
        file = docroot.joinpath(*rel.parts).resolve()
        if not file.is_relative_to(docroot) or not file.is_file():
            raise ValueError("Documentation file not found")
        if file.suffix.lower() not in (".html", ".htm", ".txt", ".md", ".xml"):
            raise ValueError("Only textual documentation can be read")
        if file.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("Documentation page too large")
        data = file.read_text(encoding="utf-8", errors="replace")
        if file.suffix.lower() in (".html", ".htm"):
            soup = BeautifulSoup(data, "html.parser")
            for n in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
                n.decompose()
            # Render code examples as fenced blocks so the agent retains Python/code indentation.
            # Process <pre> before inline <code>, otherwise nested <code> would lose formatting.
            for pre in list(soup.find_all("pre")):
                raw_code = pre.get_text("", strip=False).strip("\n")
                pre.replace_with(soup.new_string("\n\n```\n" + raw_code + "\n```\n\n"))
            for inline in list(soup.find_all("code")):
                inline.replace_with(soup.new_string("`" + inline.get_text(" ", strip=True) + "`"))
            if anchor:
                import urllib.parse
                anchor = urllib.parse.unquote(anchor)
                section = soup.find(id=anchor) or soup.find(attrs={"name": anchor})
                if section:
                    heading = section if section.name in {"h1", "h2", "h3", "h4", "h5", "h6"} else section.find_parent(re.compile(r"^h[1-6]$"))
                    current = heading or section
                    pieces = [current.get_text(" ", strip=True)]
                    for sibling in current.next_siblings:
                        if getattr(sibling, "name", "") in ("h1", "h2", "h3", "h4", "h5", "h6"):
                            break
                        if hasattr(sibling, "get_text"):
                            pieces.append(sibling.get_text(" ", strip=True))
                        elif str(sibling).strip():
                            pieces.append(str(sibling).strip())
                    text = "\n\n".join(x for x in pieces if x)
                else:
                    text = "(Anchor not found; displaying page.)\n" + (soup.main or soup.article or soup.body or soup).get_text(" ", strip=True)
            else:
                text = (soup.main or soup.article or soup.body or soup).get_text(" ", strip=True)
        else:
            text = data
        # Keep original whitespace inside code fences; normalize only surrounding prose.
        chunks = text.split("```")
        text = "```".join(re.sub(r"[ \t]+", " ", c) if i % 2 == 0 else c
                          for i, c in enumerate(chunks))
        return {"id": key, "path": path, "anchor": anchor, "content": text[:max_chars],
                "truncated": len(text) > max_chars, "characters_total": len(text)}

    def read_ref(self, ref: str, max_chars: int = 12000):
        docset, sep, path = ref.partition("::")
        if not sep:
            raise ValueError("ref must be source:name::relative/path.html#anchor")
        return self.read(docset, path, max_chars)

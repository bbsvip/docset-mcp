# Changelog

## 0.2.0 — Automatic docset management

### Added

- Configurable on-demand docset installation (`DOCSET_AUTO_INSTALL=missing`) for explicitly filtered searches and document reads. Unfiltered symbol searches remain offline-only.
- Automatic FTS5 indexing after installing a docset, FTS build on demand, and a background backfill for existing docsets (`DOCSET_AUTO_INDEX_CONTENT=true`).
- A periodic catalog refresh thread with a configurable refresh interval, a persistent cache, and offline fallback (`DOCSET_REFRESH_CATALOG_PERIODICALLY=true`).
- Docker Compose configuration and English documentation for these settings, including `docs/AUTOMATION.md`.
- `index-content --all --missing-only` to backfill existing indexes without re-downloading archives.
- Additional regression tests for automatic installation, FTS, offline catalog fallback, and CLI operations.

### Fixed

- Serializes concurrent downloads and FTS writes across Docker containers with per-docset locks.
- Recovers an installed docset directory left behind by an interrupted database update.

### Upgrade

```bash
docker compose up -d --build --force-recreate mcp
docker compose exec mcp python /app/source/app.py index-content --all --missing-only
```

The previous named volume is reused. Do not remove it with `docker compose down -v`.


## 0.1.1 — Core Data docset index compatibility

### Fixed

- Read the `ZTOKENTYPE` foreign key from `ZTOKEN` in standard Xcode/Core Data (`ZTOKEN`) indexes instead of assuming `ZTOKENMETAINFORMATION.ZTOKENTYPE` exists.
- Detect optional schema fields and older relationships in read-only indexes. This avoids dropping all symbols when docset schemas differ.
- Preserve `ZANCHOR` (and `searchIndex.fragment`, when present) in returned documentation references so `read_doc` can target the correct section.

### Added

- `reindex Python_3`: repair an installed docset by name or full `source:name` ID.
- `reindex --all --failed-only`: rebuild only symbol indexes that were previously marked unindexed, with no re-download.
- `reindex --all`: rebuild all installed docset symbol indexes.
- Regression tests for common Core Data variants and repair workflows.

### Upgrade with Docker Compose

```bash
docker compose build mcp
docker compose up -d --force-recreate mcp
docker compose exec mcp python /app/source/app.py reindex Python_3
docker compose exec mcp python /app/source/app.py search 'json.loads' --docset Python_3
```

The existing Docker volume `docset-agent-mcp-data` is reused. **Do not use `docker compose down -v`** unless you intentionally want to erase downloaded docsets.

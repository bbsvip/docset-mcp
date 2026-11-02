# Changelog

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

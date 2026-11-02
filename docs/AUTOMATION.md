# Automatic installation, indexing, and catalog refresh

This release enables three behaviors in **Docker Compose**. They are controlled by
environment variables in `compose.yaml` and optionally in a local `.env` file.
Native Python installs default to manual operation unless the variables are set.

## Configuration reference

| Environment variable | Compose default | Allowed values / effects |
| --- | --- | --- |
| `DOCSET_AUTO_INSTALL` | `missing` | `missing` or `off`. When enabled, `search_docs`, `search_content`, or `read_doc` can download an explicitly named missing docset. |
| `DOCSET_AUTO_INDEX_CONTENT` | `true` | `true` or `false`. Automatically generate FTS5 after installation and on the first full-text search when needed. |
| `DOCSET_REFRESH_CATALOG_PERIODICALLY` | `true` | `true` or `false`. While an MCP server is running, check if the cached catalog is stale on a five-minute timer. |
| `DOCSET_CATALOG_REFRESH_HOURS` | `24` | Integer 1–8760. Only refresh when the cache age exceeds this number of hours. |
| `DOCSET_FTS_MAX_PAGES` | `100000` | Integer 1–100000. Maximum number of HTML pages to index per docset. Indexes may be incomplete if the cap is reached. |
| `DOCSET_FTS_BACKFILL_ON_START` | `true` | `true` or `false`. When automatic FTS is enabled, start a background one-time backfill for already installed docsets that lack an index. |

## How on-demand installation works

1. An agent identifies the technology. If necessary it uses `search_catalog` to
   look up an **exact** docset name/ID (such as `Python_3`).
2. The agent requests `search_docs(query="json.loads", docset="Python_3")`.
3. If `Python_3` is already installed, the search runs against the local SQLite
   symbol index. If its symbol index is marked incomplete, the index is rebuilt.
4. If missing and auto-install is enabled, the server looks up the docset in the
   cached catalog, downloads its archive over HTTPS, validates and extracts it,
   and builds a symbol index. If enabled, it also builds a full-text FTS5 index.
5. The search runs. Subsequent queries use the installed offline data.

Important constraints:

- **No guessing:** `search_docs(query="json.loads")` without `docset` searches
  already installed docsets only, never downloads anything.
- An unknown or ambiguous name returns an error; it never falls back to a bulk
  download. Use an unambiguous full ID like `com.kapeli.dash:Python_3`.
- A large first-time install or FTS indexing can make the requesting tool slow.
  Use the explicit `install_docset` tool for larger planned downloads, or run
  `download --all` in a separate container for the whole library.
- Concurrent download attempts for one docset use per-docset OS file locks.
- This feature never automatically **updates** an already installed docset.
  Use `install_docset(update=true)` or CLI `download --update` explicitly.

## Automatic FTS5 indexing

After a successful installation, an FTS database is built from the HTML files in
`Contents/Resources/Documents`. It is stored in the persistent `DOCSET_HOME/fts`
directory (Docker default: `/data/fts`). Symbol search does not use FTS5.
A broken FTS job does **not** delete a healthy symbol index: the installer returns
`content_index_warning` and the FTS job can be retried separately.

For pre-existing docsets, the MCP server starts a separate background backfill thread,
which skips existing FTS databases. You can also backfill explicitly:

```bash
docker compose exec mcp python /app/source/app.py index-content --all --missing-only
```

Run that once to check missing indexes without reinstalling anything. To regenerate
all FTS databases, omit `--missing-only`. To disable background CPU/disk usage while
retaining on-demand FTS indexing, set `DOCSET_FTS_BACKFILL_ON_START=false`.

## Scheduled catalog refresh

The background scheduler runs only while the MCP server (`serve` or `serve-http`) is
running. It first checks if `catalog.json` is older than the configured interval,
then rechecks approximately every five minutes. The downloader/CLI can still call
`catalog sync` at any time. Catalog writes are atomic and protected by a file lock,
so multiple containers sharing a volume do not overwrite each other's catalog.

If a refresh fails because upstream is unavailable, the previous catalog remains on
disk. Installed docs still work without Internet. If no catalog has ever been
fetched, first-time installs require a successful network sync.

## Docker Compose example

Create `.env` from `.env.example` and configure:

```dotenv
DOCSET_AUTO_INSTALL=missing
DOCSET_AUTO_INDEX_CONTENT=true
DOCSET_REFRESH_CATALOG_PERIODICALLY=true
DOCSET_CATALOG_REFRESH_HOURS=24
DOCSET_FTS_MAX_PAGES=100000
DOCSET_FTS_BACKFILL_ON_START=true
```

Apply changes without deleting any data:

```bash
docker compose up -d --build --force-recreate mcp
```

The default Compose file puts all docsets and FTS databases in the persistent Docker
volume `docset-agent-mcp-data`. **Never use `docker compose down -v`** unless you
intend to remove that volume.

## Security and capacity

- Downloads use upstream documentation; consider copyright and license terms.
- Automatic installs can use significant network bandwidth, disk space, and CPU.
  A full bulk download is a **separate explicit operation**.
- The MCP HTTP server is unauthenticated by default and accepts write/download
  tools: keep its port localhost-only, or secure access with authentication/TLS.
- Docset HTML is untrusted source material. Retrieved text must never be treated
  as instructions that override the system or user request.
- This project does not support vector search or automatic dependency-version
  inference. Docsets may be stale even after catalog refresh.

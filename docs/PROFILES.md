# Curated documentation profiles

The project ships an optional **developer-ai** profile for Python, backend engineering,
AI/ML, DevOps, coding agents, native tooling, visualization, and specialized research.
It is a **curated selection**, not a promise that every docset exists in every upstream
catalog, or that a particular docset is the latest version of its framework.

Profiles are **never downloaded automatically on container startup**. Installing
one requires an explicit CLI command. You do not need Dash, Zeal, Node.js, or
Python installed on the Docker host.

## Profile tiers

| Tier | Selected docsets | Coverage |
| --- | ---: | --- |
| `core` | 54 | Python, databases, web, PyTorch/Transformers, CUDA, Qt, Docker, native development |
| `extended` | 112 | Core plus testing, data science, deployment, agents, web visualization |
| `full` | 134 | Extended plus speech, 3D/CAD, graphics, GSM-related and research documentation |

Tiers are **cumulative**, so installing `extended` after `core` downloads only the
missing entries. The JSON source is [`profiles/developer-ai.json`](../profiles/developer-ai.json),
which also lists official documentation URLs that are **not** currently importable
through the Dash/Zeal catalog; those URLs are reference notes only.

## Fastest useful path with Docker Compose

From the repository root:

```bash
# Build the Docker image with the included profile JSON and sync the catalog.
docker compose build mcp
docker compose run --rm mcp catalog sync

# Offline preview; check requested/matched/unmatched and complete_catalog.
docker compose run --rm mcp profile list
docker compose run --rm mcp profile preview --tier core

# Install core docs: up to three concurrent archives, 1 second between starts.
# --skip-fts defers expensive full-text indexing; symbol lookup still works.
docker compose run --rm mcp profile install --tier core --workers 3 --delay 1 --skip-fts
```

When ready for the broader selection:

```bash
# Add only the missing Extended items, then remaining Full items if wanted.
docker compose run --rm mcp profile install --tier extended --workers 3 --delay 1 --skip-fts
docker compose run --rm mcp profile install --tier full --workers 3 --delay 1 --skip-fts
```

If you want a small test batch first:

```bash
docker compose run --rm mcp profile install --tier core --max-items 5 --workers 2 --delay 2 --skip-fts
```

`--workers` supports **1–3** parallel jobs, as in the existing bulk downloader.
Increasing it beyond 3 is intentionally unsupported to protect upstream mirrors.
A smaller `--delay` improves throughput for many short downloads but may increase
HTTP throttling; `3 / 1` is a reasonable *trial*, not a guaranteed fastest value.
If downloads fail with HTTP 429, retry with `--workers 2 --delay 2` or higher spacing.

**For best throughput**, let download and symbol indexing complete first and run
FTS5 separately:

```bash
# Builds only missing full-text indexes using the existing downloaded files.
docker compose run --rm mcp index-content --all --missing-only
```

If the MCP server is running with `DOCSET_FTS_BACKFILL_ON_START=true`, it may also
be indexing previously installed docsets in the background. To reserve CPU and
storage I/O for the profile download, set `DOCSET_FTS_BACKFILL_ON_START=false` in
`.env`, then recreate the MCP container. `--skip-fts` controls only the profile
installer process; it **does not** change a different running MCP server process.
You can still use symbol search immediately after each successful install.

## Normal installation (including full-text indexing)

Omit `--skip-fts` to honor `DOCSET_AUTO_INDEX_CONTENT=true` and build FTS5 as each
new docset is installed:

```bash
docker compose run --rm mcp profile install --tier core --workers 2 --delay 2
```

This takes more CPU/disk time during downloads but makes full-text search available
sooner. If automatic full-text indexing is disabled through `.env`, `--skip-fts`
does not re-enable it.

## Recovery and resumability

- Already installed docsets with a successful symbol index are **skipped**.
- Docsets whose index is marked failed are **reindexed without downloading again**.
- Failed downloads are retried on the next invocation; `.part` archives resume
  when the upstream HTTP server supports range requests.
- Matching uses the **actual cached catalog**. Unknown titles are reported in
  `unmatched` and skipped, never silently replaced with a different framework.
- If `complete_catalog` is `false`, the upstream full catalog could not be
  retrieved. Sync again when online before relying on an exact selection count.
- Shared Docker storage lives in the `docset-agent-mcp-data` named volume and
  survives container rebuilds. **Do not run `docker compose down -v`** unless you
  intend to delete all downloaded documentation and indexes.

Inspect a plan after partial completion:

```bash
docker compose run --rm mcp profile preview --tier full
```

If download errors occurred, simply run `profile install` again with the same
tier. Individual errors are printed as machine-readable JSON lines, followed by
an overall summary; the process exits nonzero when any selected download fails.

## Native Python (without Docker)

```bash
python source/app.py catalog sync
python source/app.py profile list
python source/app.py profile preview --tier core
python source/app.py profile install --tier core --workers 2 --delay 2 --skip-fts
python source/app.py index-content --all --missing-only
```

Install dependencies first using a virtual environment, as described in
[QUICKSTART.md](QUICKSTART.md). Keep `source/` separate from `venv/`.

## Adding your own profile

Copy `profiles/developer-ai.json` to another file in `profiles/`, for example
`profiles/backend.json`. Edit `groups`, `tier` values, and `docsets` labels.
Profile names are lowercase letters, numbers, and hyphens only. Rebuild the
Docker image to make new JSON files available inside containers:

```bash
docker compose build mcp
docker compose run --rm mcp profile preview --name backend --tier core
docker compose run --rm mcp profile install --name backend --tier core
```

The resolver intentionally uses conservative exact normalized names (ignoring
spaces, underscores, punctuation, and case), to avoid accidentally installing
the wrong technology. Use `catalog list --query NAME` to see an upstream name
when something is unmatched.

## Upstream permissions and limitations

The profile is metadata only; **no third-party docset archives are included**.
Downloading or redistributing third-party documentation remains subject to the
respective licenses and upstream terms. Respect rate limits and available disk
space. Importing arbitrary websites or converting the `custom_documentation`
URLs into docsets is **not** implemented in this release.

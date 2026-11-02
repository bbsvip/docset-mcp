# Docset Agent MCP

**Offline API documentation for AI coding agents — without installing Dash or Zeal.**

Docset Agent MCP is a standalone [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server and command-line tool for downloading, indexing, and searching documentation in the Dash/Zeal `.docset` format. It supports both **Streamable HTTP** (a persistent Docker service) and **stdio** (launched by your MCP client).

> **Project status:** Early release. Downloader, archive safety, search, and transport dispatch have been tested with offline fixtures. Live upstream downloads, a real Docker build, and end-to-end MCP client connections have **not** yet been verified in the release environment. See [Testing and limitations](#testing-and-limitations).

## Highlights

- **No Dash, Zeal, Node.js, or host-side Python required** when running with Docker.
- Discover and download a single docset or **the entire upstream catalog** (with rate limiting and resumable downloads).
- Search API symbols offline through SQLite; read an individual HTML page or section with a bounded response length.
- Optional SQLite **FTS5** indexing/search for documentation page content (not semantic search).
- MCP tools for searching, reading, catalog updates, installation, and bulk-download progress.
- Runs on Windows, macOS, and Linux wherever Python or Docker is supported.
- Persistent documentation stored outside `source/` and Python virtual environments.

**Note:** This project distributes **code only**, not third-party documentation. Upstream docsets have their own usage restrictions. Read [Licensing and upstream usage](#licensing-and-upstream-usage) before downloading or distributing content.

## Quick start: Docker Compose

Docker Engine with Compose v2 (or Docker Desktop) is required.

```bash
# From the repository root
docker compose build
docker compose up -d mcp

# Sync the downloadable catalog
docker compose run --rm mcp catalog sync

# Inspect available docsets and download one for testing
docker compose run --rm mcp catalog list --query Python
docker compose run --rm mcp download Python_3

# Search the installed docset
docker compose exec mcp python /app/source/app.py search 'json.loads' --docset Python_3
```

Your MCP endpoint is **`http://127.0.0.1:8765/mcp`**. The default Compose configuration binds it **only to the local host** and does not provide authentication.

### Connect an MCP client

For [Pi](https://pi.dev/) with native MCP support:

```bash
pi mcp add docsets --url http://127.0.0.1:8765/mcp
pi mcp list
```

For another MCP client, add a **Streamable HTTP** connection pointing to the same URL. If your client supports only stdio, see [Docker stdio mode](docs/DOCKER.md#stdio-mode-no-http-port).

### Download the full catalog (optional)

**Warning:** The complete library can consume a very large amount of disk space and network bandwidth. Verify your storage capacity and your right to download the content first.

```bash
# Preview download scope; verify complete_catalog: true
docker compose run --rm mcp download --all --dry-run

# Run the bulk downloader as a background service
docker compose --profile bulk up -d downloader
docker compose logs -f downloader
```

Re-run the downloader after interruptions; installed docsets are skipped unless you specify `--update`. The downloaded files, catalog, indexes, and logs are stored in the Docker volume **`docset-agent-mcp-data`**, not in the image.

**More detailed setup:** [Docker guide](docs/DOCKER.md) · [Quick-start and CLI guide](docs/QUICKSTART.md) · [Agent prompt and usage guidance](docs/AGENT_PROMPT.md)

## MCP tools

| Tool | Purpose |
| --- | --- |
| `list_installed` | List installed docsets, recorded versions, and symbol counts. |
| `search_docs` | Find indexed names of functions, classes, and other API symbols. |
| `read_doc` | Read a page or the section referenced by a search result. |
| `search_catalog` | Search the locally cached upstream catalog. |
| `refresh_catalog` | Refresh the upstream catalog (network required). |
| `install_docset` | Download and index one docset (network and disk write). |
| `bulk_download_start` | Launch a local bulk downloader subprocess. |
| `bulk_download_status` | Inspect bulk-download progress and recent log lines. |
| `reindex_docset` | Rebuild symbol indexes from an installed docset. |
| `index_content` | Build an optional full-text SQLite FTS5 index for one docset. |
| `search_content` | Search the full-text index, if built. |

A named MCP prompt, **`documentation_research`**, is also exposed to clients that support MCP prompts. It provides an API-research workflow for the model. Server instructions and tool descriptions are written in English; see [Agent guide](docs/AGENT_PROMPT.md).

### Example agent requests

> Search the installed Python documentation for `json.loads` and explain its parameters.

> Find the PostgreSQL docset, locate JSONB indexing guidance, and include the exact reference and docset version in your answer.

> Check which local docset documents the API I'm using. If the documentation is older than my dependency, warn me before suggesting code.

MCP documentation references use the form `source:name::relative/path.html#anchor`. Use the returned `ref` exactly when calling `read_doc`; don't invent paths or fragment IDs.

## Run without Docker

Python 3.11+ is required. Install dependencies in a **virtual environment**, separately from the `source/` directory:

```bash
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python source/app.py catalog sync
./venv/bin/python source/app.py download Python_3
./venv/bin/python source/app.py search 'json.loads' --docset Python_3
```

On Windows, substitute `py -3 -m venv venv` and `venv\Scripts\python.exe` for the equivalent commands. The `setup.sh` and `setup.ps1` scripts can create the environment and optionally register Pi for stdio MCP. No `pip install -e .` is used.

For stdio, set your MCP client command to the absolute path of the venv Python executable with arguments `/absolute/path/source/app.py`, `serve`. The command **must not** use a pseudo-terminal. For HTTP without Docker, run `python source/app.py serve-http --host 127.0.0.1 --port 8765`.

## Data and configuration

| Setting | Default | Description |
| --- | --- | --- |
| `DOCSET_HOME` | `/data` in Docker; OS user data dir otherwise | Persistent archives, indexes, cache, and logs. |
| `DOCSET_PORT` | `8765` | Host port used by Docker Compose. |
| `DOCSET_MAX_ARCHIVE_GB` | `4` | Maximum download size for an archive, in GiB. |
| `DOCSET_MAX_EXPANDED_GB` | `12` | Maximum expanded size for a docset, in GiB. |
| `DOCSET_MAX_FILES` | `250000` | Maximum archive entries. |
| `DOCSET_MCP_ALLOWED_HOSTS` | Loopback and Docker service hostnames | HTTP Host allowlist; adjust only for a trusted deployment. |

Copy `.env.example` to `.env` for Compose port/resource limits. **Do not publish the unauthenticated MCP endpoint to the Internet or an untrusted LAN**; tools can write files and start downloads. Use a private tunnel or an authenticated reverse proxy when remote access is necessary.

The docset catalog is sourced from the [Zeal backend](https://github.com/zealdocs/backend), whose endpoints are **not a stable public API**. If the full catalog fails, a smaller legacy catalog is used. Check `complete_catalog` before assuming that all available sources have been enumerated. Docsets may also have incomplete indexes, outdated content, or unavailable mirror URLs.

## Testing and limitations

```bash
python -m pytest -q tests
```

Offline test fixtures cover download and extraction, traversal/symlink rejection, SQLite search, HTML section extraction, full-text indexing, CLI transport dispatch, and Docker configuration. **These tests are not a substitute for a real Docker build, a live mirror download, or an MCP handshake**, which should be performed before claiming production readiness.

Current limitations: no semantic/vector search, no automated dependency-version discovery, no authentication, no docset browser GUI, and no promise that a downloaded docset is the latest version of a framework.

## Licensing and upstream usage

**Project source code:** MIT license; see [LICENSE](LICENSE). This license covers **only the original code in this repository**.

**Third-party docsets:** The [Kapeli feeds README](https://github.com/Kapeli/feeds#license) explicitly restricts third-party application use, except for personal reasons or use with Dash/Zeal, and recommends seeking permission otherwise. Individual documentation sets may have additional copyright and license restrictions. **This repository does not grant rights to third-party documentation, redistribute any docset archive, or imply affiliation with Kapeli, Dash, or Zeal.** If you plan to publish a shared service, distribute docsets, or use the data commercially, obtain the required permissions first.

Please use reasonable download concurrency and request spacing and respect upstream service policies. The upstream mirrors may change or become unavailable.

## Contributing

Issues and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) for the local workflow and testing expectations.

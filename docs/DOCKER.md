# Docker installation and operations

Docset Agent MCP runs as a self-contained container with both the MCP server and a docset downloader. **No Dash/Zeal installation, Node.js, or Python on the host** is required. Files are stored in a persistent named volume.

## Requirements

- Docker Engine + Docker Compose v2 on Linux, or Docker Desktop on Windows/macOS.
- Enough free disk space for your selected docsets; downloading all of them can require substantial storage.
- Network access for initial catalog/download operations. Search and read work offline afterward.

## 1. Start the Streamable HTTP server

From the project root (where `compose.yaml` lives):

```bash
docker compose build
docker compose up -d mcp
docker compose ps
```

LAN endpoint (when the Docker host is `192.168.1.11`): `http://192.168.1.11:8777/mcp`. Localhost on the Docker host: `http://127.0.0.1:8777/mcp`.

The server publishes host port **8777** on all interfaces (`0.0.0.0:8777:8765`). The container continues to listen on port **8765**. By default, the Compose service also auto-installs explicitly requested docsets, auto-indexes their content, and periodically refreshes the catalog; see [AUTOMATION.md](AUTOMATION.md). To use a different host port, edit the `ports` mapping in `compose.yaml`. The path `/mcp` remains unchanged.

**Security:** this server has no HTTP authentication. Restrict access to the trusted LAN using the host firewall and never forward port 8777 on the Internet-facing router. MCP Host validation checks the server address in the request header; it does **not** filter clients by their IP address.

### Configure Pi

```bash
pi mcp add docsets --url http://192.168.1.11:8777/mcp
pi mcp list
```

Pi may run on any device able to reach the trusted LAN address. If the Docker host uses another IP, set `DOCSET_MCP_ALLOWED_HOSTS` in `.env` to include that **server** IP both with and without `:*` (for example `192.168.1.20,192.168.1.20:*`) and restart the service. If Pi runs in a container on the same Compose network, the internal endpoint is `http://mcp:8765/mcp`; the default Host allowlist includes `mcp`.

Other clients supporting **Streamable HTTP** can use the same endpoint. Avoid registering the HTTP and stdio variants under different names in the same agent unless you intentionally want duplicate tools.

## 2. Sync the catalog and download docsets

```bash
# Refresh catalog from upstream
docker compose run --rm mcp catalog sync

# Search the catalog and preview the full download
docker compose run --rm mcp catalog list --query Python
docker compose run --rm mcp download --all --dry-run

# Test with a single docset
docker compose run --rm mcp download Python_3

# Download everything in the foreground (may take a long time)
docker compose run --rm mcp download --all --workers 2 --delay 2
```

Check the `complete_catalog` field in the dry-run output. If it is `false`, the server is using a smaller fallback catalog and **does not cover every known docset**.

### Background downloader

```bash
docker compose --profile bulk up -d downloader
docker compose logs -f downloader
docker compose ps -a
```

The downloader is a separate one-shot service. It exits after completing (or failing), while the MCP server stays up. To try again after an interruption:

```bash
docker compose --profile bulk up -d --force-recreate downloader
```

Already installed docsets are skipped. Interrupted `.part` downloads are reused where the upstream HTTP server supports byte-range requests. For only a small batch, use `download --all --max-items 20` in place of the full download. Use `--update` only when you intend to re-download existing docsets.

## 3. Manage documentation and indexes

```bash
# List installed docsets
docker compose exec mcp python /app/source/app.py installed

# Repair previously installed docsets whose SQLite index is missing
# (no re-download; the persistent volume is reused)
docker compose exec mcp python /app/source/app.py reindex --all --failed-only

# Or reindex one docset by name
docker compose exec mcp python /app/source/app.py reindex Python_3

# Find an API symbol
docker compose exec mcp python /app/source/app.py search 'json.loads' --docset Python_3

# Optional: build a full-text index for this docset
docker compose exec mcp python /app/source/app.py index-content Python_3

# Query the full-text index
docker compose exec mcp python /app/source/app.py search-content 'deserialize JSON' --docset Python_3
```

Symbol search works without FTS5. **FTS5 indexing is automatic by default in Docker**:
after each install, on the first `search-content` request if an index is missing, and via a
one-time background backfill when the MCP server starts. Indexing *every* HTML page in
*every* docset requires additional CPU and disk space. To disable it, set
`DOCSET_AUTO_INDEX_CONTENT=false`. To prevent startup backfill while keeping automatic
indexing of newly downloaded docsets, use `DOCSET_FTS_BACKFILL_ON_START=false`.

```bash
# Explicitly rebuild missing full-text indexes (no docset downloads)
docker compose exec mcp python /app/source/app.py index-content --all --missing-only
```

**Automatic on-demand installation** is enabled in Compose. For example:

```bash
# Even if Python_3 was not previously installed, the filtered search can install it.
docker compose exec mcp python /app/source/app.py search 'json.loads' --docset Python_3
```

MCP `search_docs` behaves the same way with a nonempty `docset` argument. Unfiltered
searches never trigger a download. See [AUTOMATION.md](AUTOMATION.md) for all settings,
including the 24-hour catalog refresh and offline fallback.


## 4. Docker without Compose

The following commands use POSIX shell line continuation (`\`). On Windows PowerShell, use one line or the PowerShell backtick instead.

```bash
docker build -t docset-agent-mcp:local .
docker volume create docset-agent-mcp-data

docker run -d --name docset-agent-mcp --restart unless-stopped \
  -p 0.0.0.0:8777:8765 \
  -e DOCSET_MCP_ALLOWED_HOSTS="127.0.0.1,127.0.0.1:*,localhost,localhost:*,mcp,mcp:*,192.168.1.11,192.168.1.11:*" \
  -v docset-agent-mcp-data:/data \
  -e DOCSET_AUTO_INSTALL=missing \
  -e DOCSET_AUTO_INDEX_CONTENT=true \
  -e DOCSET_REFRESH_CATALOG_PERIODICALLY=true \
  -e DOCSET_CATALOG_REFRESH_HOURS=24 \
  -e DOCSET_FTS_BACKFILL_ON_START=true \
  docset-agent-mcp:local

# Sync and install using short-lived containers attached to the same volume
docker run --rm -v docset-agent-mcp-data:/data docset-agent-mcp:local catalog sync
docker run --rm -v docset-agent-mcp-data:/data docset-agent-mcp:local download Python_3
```

You can also run a separate background downloader:

```bash
docker run -d --name docset-downloader \
  -v docset-agent-mcp-data:/data \
  docset-agent-mcp:local download --all --workers 2 --delay 2
docker logs -f docset-downloader
# After the downloader exits, remove its container before starting it again:
docker rm docset-downloader
```

Do **not** start a separate HTTP container on host port 8777 at the same time as the Compose `mcp` service.

## 5. Stdio mode (no HTTP port)

An MCP client can launch the container on demand. Use the same **persistent volume name** for the docs downloaded previously:

```bash
pi mcp add docsets-docker -- docker run --rm -i \
  --mount type=volume,source=docset-agent-mcp-data,target=/data \
  docset-agent-mcp:local serve
```

PowerShell (single line):

```powershell
pi mcp add docsets-docker -- docker run --rm -i --mount type=volume,source=docset-agent-mcp-data,target=/data docset-agent-mcp:local serve
```

**`-i` is required and `-t` must not be used**. A pseudo-terminal corrupts the framing of stdio MCP messages. Stdio mode launches a separate container process for the MCP client rather than accessing the persistent HTTP service.

## 6. Storage, upgrades, and security

Docker uses the named volume `docset-agent-mcp-data`, which persists after `docker compose down` and container/image recreation. **Do not run `docker compose down -v` if you want to keep the docsets.**

```bash
# Inspect the data volume
docker volume inspect docset-agent-mcp-data

# Rebuild after editing the source
docker compose build
docker compose up -d mcp
```

For host bind mounts on Linux, the container runs under UID/GID `10001:10001`. Ensure that the destination directory is writable by this user before changing `compose.yaml`. For Windows, use a path such as `D:/DocsetData:/data` if a bind mount is preferred; named volumes are usually simpler for large collections of small files.

### Security

- The HTTP MCP server **has no authentication** and exposes download/write tools.
- Do not publish its port to the public Internet or an untrusted LAN. Use a protected tunnel or authenticated TLS proxy for remote connections.
- Host/Origin validation is enabled; `DOCSET_MCP_ALLOWED_HOSTS` permits loopback, the Docker service hostname, and `192.168.1.11` by default. Configure your own **server** IP if different. This is not client-IP filtering.
- The remote catalog API is upstream-dependent, and docset archives should be treated as untrusted inputs.

## 7. Troubleshooting

| Symptom | Check |
| --- | --- |
| Connection refused on port 8777 | `docker compose ps`, `docker compose logs mcp`, and the `0.0.0.0:8777:8765` port mapping; check host firewall rules. |
| HTTP 421 Invalid Host header | Put the Docker **server** IP in `DOCSET_MCP_ALLOWED_HOSTS` (bare and `:*`), then `docker compose up -d --force-recreate mcp`. |
| `catalog sync` fails | Network/DNS, upstream availability, and the Zeal backend API. |
| `complete_catalog: false` | A smaller legacy fallback catalog was used; bulk download is not complete coverage. |
| Archive too large | `DOCSET_MAX_ARCHIVE_GB` / `DOCSET_MAX_EXPANDED_GB` and free disk space. |
| MCP tools visible but no search results | With `docset` set, an absent docset is automatically installed (if enabled). For existing ones check `indexed: 0` or `symbols: 0`; run `reindex --all --failed-only` if needed. |
| `no such column: m.ZTOKENTYPE` | Old builds used an incorrect Core Data join. Rebuild the image with the fixed indexer and run `reindex Python_3`. Your docset volume is preserved. |
| Download fails for one docset | Inspect logs; mirrors, archive structure, or index schemas may have changed. The first filtered query can fail if auto-installation fails. |
| `Docset not installed ... (auto-install disabled)` | Set `DOCSET_AUTO_INSTALL=missing` or explicitly download via CLI / `install_docset`. |
| Disk/CPU usage rises after upgrade | Automatic FTS backfill is enabled; set `DOCSET_FTS_BACKFILL_ON_START=false` or `DOCSET_AUTO_INDEX_CONTENT=false`. |
| Catalog cannot refresh while offline | Previously cached catalog and installed docsets remain usable; check logs when network access returns. |
| Agent in a container cannot reach localhost | Use a shared Docker network and service hostname or a properly secured host endpoint. |

### Test coverage

Offline unit tests and Compose configuration parsing have been used during development. A full `docker build`, live archive download, and MCP client handshake require validation on a machine with Docker and upstream access.

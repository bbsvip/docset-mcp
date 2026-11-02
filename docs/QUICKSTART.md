# Quick start and CLI reference

This guide covers the native Python workflow for users who do not want Docker. For Docker-first deployment, read [DOCKER.md](DOCKER.md).

## Linux and macOS

```bash
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python source/app.py catalog sync
./venv/bin/python source/app.py download Python_3
./venv/bin/python source/app.py search 'json.loads' --docset Python_3
```

Alternatively, run `./setup.sh` to install dependencies and sync the catalog. `./setup.sh --register-pi` also registers a local stdio MCP server if Pi is installed.

## Windows PowerShell

```powershell
py -3 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe source\app.py catalog sync
.\venv\Scripts\python.exe source\app.py download Python_3
.\venv\Scripts\python.exe source\app.py search 'json.loads' --docset Python_3
```

Alternatively, run `.\setup.ps1` or `.\setup.ps1 -RegisterPi`. Python 3.11+ is required. No editable package install is needed: the application runs directly from `source/app.py`.

## CLI commands

```text
python source/app.py catalog sync
python source/app.py catalog list --query Python
python source/app.py installed
python source/app.py download Python_3
python source/app.py download --all --dry-run
python source/app.py download --all --max-items 20 --workers 2 --delay 2
python source/app.py download --all --workers 2 --delay 2
python source/app.py search 'json.loads' --docset Python_3
python source/app.py read 'com.kapeli.dash:Python_3::library/json.html#json.loads'
python source/app.py index-content Python_3
python source/app.py search-content 'deserialize JSON' --docset Python_3
python source/app.py reindex Python_3
python source/app.py reindex --all --failed-only
python source/app.py reindex --all
python source/app.py serve
python source/app.py serve-http --host 127.0.0.1 --port 8765
```

The `read` reference above illustrates the format used by search results; use **the actual `ref` returned by your installation**, rather than assuming a symbol maps to a particular HTML path.

### Download options

- `--all`: select every entry in the cached catalog (subject to `--source` and `--max-items`).
- `--dry-run`: report how many entries would be downloaded, without downloading archives.
- `--max-items N`: limit a bulk run to its first N pending items.
- `--workers N`: concurrent downloads, from 1 to 3.
- `--delay SECONDS`: spacing between new download jobs.
- `--update`: re-download installed docsets rather than skipping them.
- `--source SOURCE_ID`: restrict bulk downloads to one source, for example `com.kapeli.contrib`.

A failed bulk run may exit with code 2. Re-run without `--update` to skip successful installations and retry unsuccessful items. The local index does not require any server to stay running. `reindex Python_3` rebuilds an already installed symbol index without downloading anything. `reindex --all --failed-only` retries all installed docsets marked unindexed, while `reindex --all` forces a full symbol-index rebuild.

## Store data elsewhere

Set `DOCSET_HOME` **before** downloading and in the environment of the MCP server so both processes use the same library:

```bash
export DOCSET_HOME=/mnt/storage/docsets
```

```powershell
$env:DOCSET_HOME = 'D:\DocsetData'
```

Default locations: `%LOCALAPPDATA%\DocsetAgentMCP` on Windows; `$XDG_DATA_HOME/docset-agent-mcp` or `~/.local/share/docset-agent-mcp` on Unix-like systems. Keep these files outside both `source/` and `venv/`.

## Connect to Pi with local stdio

```bash
pi mcp add docsets -- /absolute/path/to/venv/bin/python /absolute/path/to/source/app.py serve
pi mcp list
```

On Windows use absolute Windows paths to `venv\Scripts\python.exe` and `source\app.py`. For other MCP clients, configure the equivalent stdio command in the client's MCP settings. Do not run `serve` in an interactive terminal expecting ordinary text: standard output is used for MCP protocol messages.

## Recommended research flow

1. List installed docsets, and identify the closest matching technology and version.
2. Search exact symbols with `search_docs` before using broad full-text queries.
3. Read the returned references with `read_doc`, using a bounded `max_chars` value.
4. Build a full-text index (`index_content`) only if symbol search is insufficient.
5. State the docset/version used and warn about any dependency version mismatch.

See [AGENT_PROMPT.md](AGENT_PROMPT.md) for a ready-to-copy agent instruction.

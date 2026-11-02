"""MCP SDK v2 server: stdio for local agents, Streamable HTTP for Docker."""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

from docsets import DocsetStore
from agent_prompts import SERVER_INSTRUCTIONS, documentation_research_prompt


def run_server(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8765):
    from mcp.server import MCPServer

    store = DocsetStore()
    mcp = MCPServer("docset-agent-mcp", instructions=SERVER_INSTRUCTIONS)

    @mcp.prompt()
    def documentation_research(technology: str, task: str = "") -> str:
        """Research a framework API using installed, version-aware offline docs."""
        return documentation_research_prompt(technology, task)

    @mcp.tool()
    def list_installed(limit: int = 100) -> list[dict]:
        """List docsets installed locally, including version and symbol count."""
        return store.installed(limit)

    @mcp.tool()
    def search_docs(query: str, docset: str = "", limit: int = 20) -> list[dict]:
        """Search offline indexed APIs/symbol names; optionally restrict to an installed docset."""
        return store.search(query, docset, limit)

    @mcp.tool()
    def read_doc(ref: str, max_chars: int = 12000) -> dict:
        """Read one HTML documentation page or symbol fragment by its search result ref."""
        return store.read_ref(ref, max_chars)

    @mcp.tool()
    def search_catalog(query: str = "", limit: int = 50) -> dict:
        """List/search all downloadable docsets in local catalog (requires catalog sync once)."""
        return store.catalog_search(query, limit)

    @mcp.tool()
    def refresh_catalog() -> dict:
        """Download current list of all Dash/Zeal docsets from upstream API."""
        return store.sync_catalog()

    @mcp.tool()
    def install_docset(name: str, source: str = "", update: bool = False) -> dict:
        """Download and index a named docset. Can be a large network/disk operation."""
        return store.download_one(name, source or None, update)

    @mcp.tool()
    def bulk_download_start(workers: int = 2, delay_seconds: float = 2.0, update: bool = False) -> dict:
        """Start full-catalog download as a detached local CLI process; monitor with bulk_download_status.

        No Dash or Zeal installation needed. Downloads sequentially by default and automatically
        resumes on rerun by skipping installed docsets.
        """
        if not 1 <= workers <= 3 or not 0 <= delay_seconds <= 120:
            raise ValueError("workers must be 1..3 and delay_seconds must be 0..120")
        home = store.home
        pidfile = home / "bulk.pid"
        logfile = home / "bulk.log"
        if pidfile.exists():
            pid = pidfile.read_text().strip()
            if pid.isdigit() and is_running(int(pid)):
                return {"status": "already_running", "pid": int(pid), "log": str(logfile)}
        command = [sys.executable, str(Path(__file__).resolve().parent / "app.py"), "download", "--all",
                   "--workers", str(workers), "--delay", str(delay_seconds)]
        if update:
            command.append("--update")
        # Standard subprocess isolation: MCP call returns promptly while local process does the work.
        with logfile.open("a", encoding="utf-8") as fd:
            fd.write("\nStarting bulk download...\n")
            fd.flush()
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
            p = subprocess.Popen(command, stdout=fd, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, cwd=str(home),
                                 creationflags=flags, start_new_session=sys.platform != "win32")
        pidfile.write_text(str(p.pid), encoding="ascii")
        return {"status": "started", "pid": p.pid, "log": str(logfile), "home": str(home)}

    @mcp.tool()
    def bulk_download_status(tail_lines: int = 15) -> dict:
        """Show current batch progress and recent log lines."""
        pidfile = store.home / "bulk.pid"
        logfile = store.home / "bulk.log"
        pid = int(pidfile.read_text()) if pidfile.exists() and pidfile.read_text().strip().isdigit() else None
        lines = logfile.read_text(encoding="utf-8", errors="replace").splitlines() if logfile.exists() else []
        return {"pid": pid, "running": bool(pid and is_running(pid)),
                "installed": len(store.installed(2000)), "recent_log": lines[-max(1, min(tail_lines, 100)):],
                "log": str(logfile)}

    @mcp.tool()
    def index_content(docset: str, max_pages: int = 10000) -> dict:
        """Build full-text FTS5 index of HTML pages for ONE docset (disk/CPU intensive)."""
        return store.index_content(docset, max_pages)

    @mcp.tool()
    def search_content(query: str, docset: str, limit: int = 10) -> list[dict]:
        """Search FULL HTML documentation text in a docset that has a content index."""
        return store.search_content(query, docset, limit)

    @mcp.tool()
    def reindex_docset(docset_id: str) -> dict:
        """Rebuild symbol index for an installed docset by its source:name identifier."""
        return store.reindex_one(docset_id)

    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport == "streamable-http":
        # Docker binds 0.0.0.0 inside the container. Validate Host/Origin explicitly
        # rather than relying on SDK defaults that vary with the bind address.
        from mcp.server.transport_security import TransportSecuritySettings

        hosts = [x.strip() for x in os.environ.get(
            "DOCSET_MCP_ALLOWED_HOSTS",
            "127.0.0.1,127.0.0.1:*,localhost,localhost:*,[::1],[::1]:*,mcp,mcp:*",
        ).split(",") if x.strip()]
        security = TransportSecuritySettings(
            allowed_hosts=hosts,
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        )
        # Stateless JSON responses make clients simpler; this is for trusted local use only.
        mcp.run(transport="streamable-http", host=host, port=port,
                stateless_http=True, json_response=True, transport_security=security)
    else:
        raise ValueError(f"Unsupported MCP transport: {transport}")


def is_running(pid: int) -> bool:
    if pid < 1:
        return False
    try:
        import os
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False

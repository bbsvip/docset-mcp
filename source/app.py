"""Command-line entry point. Run directly: python source/app.py <command>."""
import argparse
import json
import sys

from docsets import DocsetStore


def main():
    parser = argparse.ArgumentParser(description="Standalone offline docset archive + MCP server")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="Run stdio MCP server (for Docker stdin/stdout clients)")
    http = commands.add_parser("serve-http", help="Run Streamable HTTP MCP server")
    http.add_argument("--host", default="127.0.0.1", help="Bind address (Docker: 0.0.0.0)")
    http.add_argument("--port", type=int, default=8765)
    catalog = commands.add_parser("catalog", help="Sync/list downloadable docsets")
    catalog.add_argument("action", choices=("sync", "list"))
    catalog.add_argument("--query", default="")
    catalog.add_argument("--limit", type=int, default=50)
    commands.add_parser("installed", help="List installed docsets")
    download = commands.add_parser("download", help="Download docsets (single or whole catalog)")
    download.add_argument("name", nargs="?", default="")
    download.add_argument("--all", action="store_true")
    download.add_argument("--source", default="", help="Optional source ID")
    download.add_argument("--workers", type=int, default=2)
    download.add_argument("--delay", type=float, default=2.0)
    download.add_argument("--max-items", type=int, default=0)
    download.add_argument("--update", action="store_true")
    download.add_argument("--dry-run", action="store_true")
    search = commands.add_parser("search", help="Search offline symbols")
    search.add_argument("query")
    search.add_argument("--docset", default="")
    search.add_argument("--limit", type=int, default=20)
    read = commands.add_parser("read", help="Read one symbol/document reference")
    read.add_argument("ref", help="source:name::relative/path.html#anchor")
    read.add_argument("--max-chars", type=int, default=12000)
    fts = commands.add_parser("index-content", help="Build optional full-text index for one docset")
    fts.add_argument("docset")
    fts.add_argument("--max-pages", type=int, default=10000)
    fts_search = commands.add_parser("search-content", help="Search an indexed docset body")
    fts_search.add_argument("query")
    fts_search.add_argument("--docset", required=True)
    fts_search.add_argument("--limit", type=int, default=10)
    reindex = commands.add_parser("reindex", help="Rebuild symbol index")
    reindex.add_argument("id", nargs="?", help="Installed docset name or full ID")
    reindex.add_argument("--all", action="store_true", help="Reindex every installed docset")
    reindex.add_argument("--failed-only", action="store_true", help="With --all, only retry docsets with failed/missing indices")
    args = parser.parse_args()
    if args.command in ("serve", "serve-http"):
        from mcp_server import run_server
        if args.command == "serve":
            run_server(transport="stdio")
        else:
            if not 1 <= args.port <= 65535:
                parser.error("--port must be between 1 and 65535")
            run_server(transport="streamable-http", host=args.host, port=args.port)
        return
    store = DocsetStore()
    if args.command == "catalog":
        output = store.sync_catalog() if args.action == "sync" else store.catalog_search(args.query, args.limit)
    elif args.command == "installed":
        output = store.installed()
    elif args.command == "download":
        if args.all:
            output = store.download_all(workers=args.workers, delay=args.delay, max_items=args.max_items,
                                        source=args.source, update=args.update, dry_run=args.dry_run,
                                        log=lambda e: print(json.dumps(e, ensure_ascii=False), flush=True))
        else:
            if not args.name:
                parser.error("Provide a docset name or --all")
            output = store.download_one(args.name, args.source or None, args.update)
    elif args.command == "search":
        output = store.search(args.query, args.docset, args.limit)
    elif args.command == "read":
        output = store.read_ref(args.ref, args.max_chars)
    elif args.command == "reindex":
        if args.all:
            if args.id:
                parser.error("Do not supply an ID with --all")
            output = store.reindex_all(failed_only=args.failed_only,
                                       log=lambda e: print(json.dumps(e, ensure_ascii=False), flush=True))
        else:
            if not args.id:
                parser.error("Provide a docset name or --all")
            if args.failed_only:
                parser.error("--failed-only requires --all")
            output = store.reindex_one(args.id)
    elif args.command == "index-content":
        output = store.index_content(args.docset, args.max_pages)
    elif args.command == "search-content":
        output = store.search_content(args.query, args.docset, args.limit)
    else:
        parser.error("Unknown command")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if ((args.command == "download" and args.all) or (args.command == "reindex" and args.all)) and output.get("failed"):
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

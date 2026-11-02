"""Built-in curated docset profiles. Direct-file import; no Python package installation.

Profiles are installed explicitly from the CLI, never at server startup.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
import threading
import time


PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles"
TIERS = {"core": 1, "extended": 2, "full": 3}
SOURCE_PRIORITY = {
    "com.kapeli.dash": 0,
    "com.kapeli": 0,
    "com.kapeli.contrib": 1,
    "com.kapeli.cheatsheet": 2,
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold().replace("+", "plus").replace("#", "sharp"))


def load_profile(name: str = "developer-ai") -> dict:
    """Read only profiles shipped with the application (no arbitrary file paths)."""
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        raise ValueError("Invalid profile name")
    path = PROFILE_DIR / (name + ".json")
    if not path.is_file():
        raise ValueError(f"Unknown profile {name!r}. Available profiles: {', '.join(list_profiles())}")
    profile = json.loads(path.read_text(encoding="utf-8"))
    groups = profile.get("groups", [])
    if not isinstance(groups, list) or any(
        not isinstance(g, dict) or g.get("tier") not in (1, 2, 3) or
        not isinstance(g.get("docsets"), list) or
        any(not isinstance(v, str) or not v.strip() for v in g["docsets"])
        for g in groups
    ):
        raise ValueError("Invalid built-in profile definition")
    return profile


def list_profiles() -> dict:
    return {p.stem: p.name for p in sorted(PROFILE_DIR.glob("*.json"))}


def describe_profiles() -> dict:
    result = []
    for name in list_profiles():
        profile = load_profile(name)
        counts = {}
        for tier, level in TIERS.items():
            counts[tier] = len(selected_titles(profile, level))
        result.append({"name": name, "title": profile.get("title", name),
                       "counts": counts, "description": profile.get("description", "")})
    return {"profiles": result, "default": "developer-ai"}


def selected_titles(profile: dict, level: int) -> list[str]:
    """Keep group order and deduplicate exact/near-identical titles."""
    titles = []
    seen = set()
    for group in profile["groups"]:
        if group["tier"] > level:
            continue
        for title in group["docsets"]:
            key = _slug(title)
            if key not in seen:
                titles.append(title)
                seen.add(key)
    return titles


def match_entry(entries: list[dict], title: str) -> dict | None:
    """Exact normalized matching only; never silently substitute another technology."""
    expected = _slug(title)
    hits = [x for x in entries if _slug(x["name"]) == expected]
    # The published Qt for Python catalog name may include a version suffix.
    if not hits and title == "Qt for Python (PySide 2/PySide 6)":
        hits = [x for x in entries if _slug(x["name"]).startswith("qtforpython")]
    if not hits:
        return None
    return min(hits, key=lambda x: (SOURCE_PRIORITY.get(x["source"], 10), x["id"]))


def plan_profile(store, name: str = "developer-ai", tier: str = "core",
                 max_items: int = 0, refresh: bool = False) -> tuple[dict, list[dict]]:
    """Resolve names against the actual cached catalog and installed database."""
    if tier not in TIERS:
        raise ValueError("Unknown tier")
    if max_items < 0:
        raise ValueError("--max-items must be >= 0")
    profile = load_profile(name)
    titles = selected_titles(profile, TIERS[tier])
    catalog = store.catalog(refresh=refresh)
    matched, unmatched = [], []
    seen = set()
    for title in titles:
        entry = match_entry(catalog["entries"], title)
        if entry is None:
            unmatched.append(title)
        elif entry["id"] not in seen:
            matched.append(entry)
            seen.add(entry["id"])
    installed = {row["id"]: row for row in store.installed(2000)}
    pending = [item for item in matched if item["id"] not in installed]
    # Reattempt failed symbol-index builds without re-downloading docsets.
    repair = [item for item in matched if item["id"] in installed and
              not installed[item["id"]].get("indexed", 0)]
    selected = pending + repair
    if max_items:
        selected = selected[:max_items]
    metadata = {
        "profile": name, "tier": tier, "requested": len(titles),
        "matched": len(matched), "installed": len(matched) - len(pending),
        "pending_downloads": len(pending), "pending_reindex": len(repair),
        "selected": len(selected), "complete_catalog": bool(catalog.get("complete_catalog")),
        "unmatched": unmatched, "ids_to_process": [e["id"] for e in selected],
    }
    if not metadata["complete_catalog"]:
        metadata["warning"] = "Catalog is partial/fallback; sync the full catalog for best coverage."
    if unmatched:
        metadata["unmatched_warning"] = "Unmatched names were skipped; inspect the catalog for alternatives."
    return metadata, selected


def install_profile(store, name: str = "developer-ai", tier: str = "core", workers: int = 2,
                    delay: float = 2.0, max_items: int = 0, refresh: bool = False,
                    skip_fts: bool = False, dry_run: bool = False, log=None) -> dict:
    if not 1 <= workers <= 3:
        raise ValueError("--workers must be 1..3 to avoid overloading upstream mirrors")
    if delay < 0:
        raise ValueError("--delay must be >= 0")
    meta, entries = plan_profile(store, name, tier, max_items, refresh)
    if log:
        log({"event": "plan", **meta})
    if dry_run or not entries:
        return {**meta, "dry_run": dry_run, "successful": 0, "failed": 0, "failures": []}

    # CLI-only override: a fast download installs searchable symbol indexes first.
    # Existing MCP server processes maintain their own settings independently.
    if skip_fts:
        store.auto_index_content = False
    installed = {row["id"]: row for row in store.installed(2000)}
    start = time.monotonic()
    gate = threading.Lock()
    next_slot = [start]

    def task(entry):
        key = entry["id"]
        if key in installed and not installed[key].get("indexed", 0):
            try:
                result = store.reindex_one(key)
                return {"id": key, "status": "reindexed", **result}
            except Exception as exc:
                return {"id": key, "status": "failed", "error": str(exc)}
        with gate:
            now = time.monotonic()
            wait = max(0.0, next_slot[0] - now)
            next_slot[0] = max(next_slot[0], now) + delay
        if wait:
            time.sleep(wait)
        try:
            return store.download_one(entry["name"], entry["source"])
        except Exception as exc:
            return {"id": key, "status": "failed", "error": str(exc)}

    failures, successful = [], 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(task, entry) for entry in entries]
        for future in as_completed(futures):
            result = future.result()
            if result.get("status") == "failed":
                failures.append(result)
            else:
                successful += 1
            if log:
                log({"event": "item", **result})
    return {**meta, "dry_run": False, "successful": successful, "failed": len(failures),
            "failures": failures[:100], "elapsed_seconds": round(time.monotonic() - start, 2),
            "fts_deferred": skip_fts}

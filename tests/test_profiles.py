"""Curated profile integration, no network access or Docker daemon required."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "source"))

import app
from profiles import describe_profiles, load_profile, match_entry, plan_profile, install_profile, selected_titles


class FakeStore:
    def __init__(self, installed=None, entries=None, complete=True):
        self._installed = installed or []
        self._entries = entries or []
        self.complete = complete
        self.downloaded = []
        self.reindexed = []
        self.auto_index_content = True
        self.refreshes = []

    def catalog(self, refresh=False):
        self.refreshes.append(refresh)
        return {"entries": self._entries, "complete_catalog": self.complete}

    def installed(self, limit=2000):
        return self._installed

    def download_one(self, name, source=None):
        self.downloaded.append((name, source, self.auto_index_content))
        if name == "Broken":
            raise RuntimeError("simulated network failure")
        return {"id": f"{source}:{name}", "status": "installed", "symbols": 9}

    def reindex_one(self, docset):
        self.reindexed.append(docset)
        return {"id": docset, "symbols": 15}


def entry(name, source="com.kapeli.dash"):
    return {"id": f"{source}:{name}", "name": name, "source": source}


def test_builtin_profile_counts_and_cumulative_tiers():
    profile = load_profile()
    assert [len(selected_titles(profile, level)) for level in (1, 2, 3)] == [54, 112, 134]
    assert describe_profiles()["profiles"][0]["counts"] == {"core": 54, "extended": 112, "full": 134}
    assert len({x.casefold() for x in selected_titles(profile, 3)}) == 134


def test_profile_names_are_safe():
    import pytest
    with pytest.raises(ValueError):
        load_profile("../../etc/passwd")
    with pytest.raises(ValueError):
        load_profile("nonexistent")


def test_match_exact_normalized_and_prefer_reference_over_cheatsheet():
    entries = [entry("Python_3", "com.kapeli.cheatsheet"), entry("Python_3"), entry("PyTorch")]
    assert match_entry(entries, "Python 3")["source"] == "com.kapeli.dash"
    assert match_entry(entries, "pytorch")["name"] == "PyTorch"
    assert match_entry(entries, "Python 2") is None
    assert match_entry([entry("C"), entry("C++"), entry("C#")], "C++")["name"] == "C++"
    assert match_entry([entry("Qt_for_Python_6")], "Qt for Python (PySide 2/PySide 6)") is not None


def test_plan_profile_reports_unmatched_and_installed():
    store = FakeStore(installed=[{**entry("Python_3"), "indexed": 1}], entries=[entry("Python_3"), entry("PyTorch")], complete=False)
    info, selected = plan_profile(store)
    assert info["requested"] == 54
    assert info["matched"] == 2
    assert info["installed"] == 1
    assert info["selected"] == 1
    assert info["complete_catalog"] is False
    assert "PostgreSQL" in info["unmatched"]
    assert [x["name"] for x in selected] == ["PyTorch"]


def test_skip_fts_and_limit_resume_no_duplicate_downloads():
    store = FakeStore(entries=[entry("Python_3"), entry("PyTorch")])
    events = []
    result = install_profile(store, workers=2, delay=0, max_items=1, skip_fts=True, log=events.append)
    assert result["selected"] == 1 and result["successful"] == 1 and result["failed"] == 0
    assert len(store.downloaded) == 1
    assert store.downloaded[0] == ("Python_3", "com.kapeli.dash", False)
    assert events[0]["event"] == "plan" and events[1]["event"] == "item"


def test_dry_run_no_downloads_and_refresh():
    store = FakeStore(entries=[entry("Python_3")])
    result = install_profile(store, dry_run=True, refresh=True, delay=0)
    assert result["dry_run"] is True
    assert store.downloaded == []
    assert store.refreshes == [True]


def test_reindex_existing_broken_index_without_download():
    store = FakeStore(installed=[{**entry("Python_3"), "indexed": 0}], entries=[entry("Python_3")])
    result = install_profile(store, workers=2, delay=0)
    assert result["successful"] == 1
    assert store.reindexed == ["com.kapeli.dash:Python_3"]
    assert store.downloaded == []


def test_cli_list_is_network_free(monkeypatch, capsys):
    def forbidden():
        raise AssertionError("DocsetStore should not be instantiated")
    monkeypatch.setattr(app, "DocsetStore", forbidden)
    monkeypatch.setattr(sys, "argv", ["app.py", "profile", "list"])
    app.main()
    assert '"full": 134' in capsys.readouterr().out


def test_cli_preview_dispatch(monkeypatch, capsys):
    store = FakeStore(entries=[entry("Python_3")])
    monkeypatch.setattr(app, "DocsetStore", lambda: store)
    monkeypatch.setattr(sys, "argv", ["app.py", "profile", "preview", "--tier", "core"])
    app.main()
    assert '"requested": 54' in capsys.readouterr().out
    assert store.downloaded == []


def test_cli_install_dispatch(monkeypatch, capsys):
    store = FakeStore(entries=[entry("Python_3")])
    monkeypatch.setattr(app, "DocsetStore", lambda: store)
    monkeypatch.setattr(sys, "argv", ["app.py", "profile", "install", "--tier", "full", "--workers", "3", "--delay", "0", "--skip-fts"])
    app.main()
    assert store.downloaded == [("Python_3", "com.kapeli.dash", False)]
    assert '"successful": 1' in capsys.readouterr().out


def test_docker_bundles_profiles():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY profiles/ ./profiles/" in dockerfile
    assert (ROOT / "profiles" / "developer-ai.json").is_file()

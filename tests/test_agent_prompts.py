"""Publicly documented English instructions for coding-agent MCP clients."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))

from agent_prompts import SERVER_INSTRUCTIONS, documentation_research_prompt


def test_server_instructions_include_version_and_trust_boundary():
    assert "version" in SERVER_INSTRUCTIONS
    assert "untrusted" in SERVER_INSTRUCTIONS
    assert "search_docs" in SERVER_INSTRUCTIONS
    assert "read_doc" in SERVER_INSTRUCTIONS


def test_named_prompt_is_task_specific_and_does_not_download():
    result = documentation_research_prompt("PostgreSQL", "Check GIN index support")
    assert "PostgreSQL" in result
    assert "Check GIN index support" in result
    assert "list_installed" in result
    assert "read_doc" in result
    assert "Do not install or bulk-download" in result

"""RAR-03: the authoritative plugin manifest must agree with the actual
deliverable - version 0.4.0 (tag v0.4.0) and 20 MCP tools (the real
@mcp.tool count in mcp_server.py).

Before RAR-03 the manifests (plugin.json + the enclosing marketplace.json)
still said version 0.3.0 / "Exposes 16 MCP tools" while the code had 20
tools and the release tag was v0.4.0 - so `claude plugin list`/`details`
reported a stale build identity and the "manifest, version/build identity,
single installable entry consistent" gate (AR-05) was not met. These tests
are the anti-drift gate the reviewer required.
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WIN = REPO / "v2_win"
PLUGIN_JSON = WIN / "cc-communicate" / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = WIN / ".claude-plugin" / "marketplace.json"
MCP_SERVER = WIN / "cc-communicate" / "server" / "mcp_server.py"

RELEASE_VERSION = "0.4.0"
TOOL_COUNT = 20


def _tool_decorators() -> int:
    return sum(1 for ln in MCP_SERVER.read_text(encoding="utf-8").splitlines()
               if ln.strip().startswith("@mcp.tool"))


def test_plugin_manifest_version_matches_release():
    """plugin.json + marketplace.json version == the release version (the
    plugin registry (`claude plugin list`) reports exactly this value)."""
    plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    assert plugin["version"] == RELEASE_VERSION
    mkt = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))
    assert mkt["metadata"]["version"] == RELEASE_VERSION
    assert [p["version"] for p in mkt["plugins"]] == [RELEASE_VERSION]


def test_plugin_manifest_tool_count_matches_server():
    """The manifest's advertised tool count equals the real number of
    @mcp.tool() registrations in mcp_server.py (drift gate: if a tool is
    added/removed without updating the manifest, this goes red)."""
    n = _tool_decorators()
    assert n == TOOL_COUNT, "mcp_server.py has %d @mcp.tool; expected %d" % (
        n, TOOL_COUNT)
    plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    mkt = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))
    assert ("%d MCP tools" % TOOL_COUNT) in plugin["description"], plugin["description"]
    assert ("%d MCP tools" % TOOL_COUNT) in mkt["plugins"][0]["description"]


def test_marketplace_points_at_canonical_tree():
    """The enclosing marketplace manifest resolves the plugin source inside
    the canonical tree (no external/legacy source)."""
    mkt = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))
    assert mkt["plugins"][0]["source"] == "./cc-communicate"
    assert (WIN / mkt["plugins"][0]["source"] / ".claude-plugin" / "plugin.json").is_file()


def test_marketplace_manifest_twins_in_sync():
    """The repo-level marketplace manifests (v2_win/ + v2_wsl/) sit OUTSIDE
    build_artifacts' mirror scope (it copies v2_win/cc-communicate only), so
    the WSL twin is hand-synced - this locks the two byte-identical (a stale
    WSL twin would report a wrong build identity on WSL installs)."""
    win = MARKETPLACE_JSON.read_text(encoding="utf-8")
    wsl = (REPO / "v2_wsl" / ".claude-plugin" / "marketplace.json").read_text(
        encoding="utf-8")
    assert win == wsl

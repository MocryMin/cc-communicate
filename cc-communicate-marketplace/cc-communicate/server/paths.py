"""Filesystem locations for the cc-communicate upper layer (kernel + user functions).

Frozen-equivalent of scripts/lib/paths.js. The first four constants
(PLUGIN_ROOT, DATA_DIR, SESSION_CTRL_DIR, DEBUG_FILE) MUST stay in sync with
paths.js — both layers read the same session_ctrl/ folder, so they must agree
on where it is. The remaining constants are upper-layer-only.

Resolution mirrors paths.js: prefer CLAUDE_PLUGIN_ROOT (set by CC when it runs
a hook / MCP server); fall back to __file__-relative so the kernel, MCP tools,
and manual `python server/kernel.py` all resolve correctly without the env var.

This file lives at <PLUGIN_ROOT>/server/paths.py — one level below the plugin
root — so a single '..' reaches it. (paths.js is two levels deep in
scripts/lib/ and uses '../..'; the resolved PLUGIN_ROOT is the same.)
"""
import os

_env_root = os.environ.get('CLAUDE_PLUGIN_ROOT')
# Fall back to __file__-relative if CLAUDE_PLUGIN_ROOT is unset OR was left as an
# unsubstituted ${...} literal (some MCP runners don't substitute env values).
PLUGIN_ROOT = _env_root if (_env_root and '${' not in _env_root) else os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..'))

# --- shared with paths.js (keep in sync) ------------------------------------
DATA_DIR         = os.path.join(PLUGIN_ROOT, 'data')
SESSION_CTRL_DIR = os.path.join(DATA_DIR, 'session_ctrl')   # append-only event log (lower layer writes)
DEBUG_FILE       = os.path.join(DATA_DIR, 'debug.log')

# --- upper-layer-only -------------------------------------------------------
SERVER_DATA_DIR     = os.path.join(DATA_DIR, 'server')        # kernel products: core_status.json, alive_sessions snapshot
QUEUE_DIR           = os.path.join(DATA_DIR, 'queue')         # RPC request files (tool -> kernel)
QUEUE_RESPONSES_DIR = os.path.join(QUEUE_DIR, 'responses')    # RPC response files (kernel -> tool)
CONVERSATIONS_DIR   = os.path.join(DATA_DIR, 'conversations') # p2p message pipes + logs

CORE_STATUS_FILE    = os.path.join(SERVER_DATA_DIR, 'core_status.json')
SESSIONS_FILE       = os.path.join(SERVER_DATA_DIR, 'sessions.json')  # persistent session registry


def ensure_runtime_dirs():
    """Create the runtime data directories the upper layer needs.
    Idempotent; safe to call on every kernel init / tool start."""
    for d in (DATA_DIR, SESSION_CTRL_DIR, SERVER_DATA_DIR,
              QUEUE_DIR, QUEUE_RESPONSES_DIR, CONVERSATIONS_DIR):
        os.makedirs(d, exist_ok=True)

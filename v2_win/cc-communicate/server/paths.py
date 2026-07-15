"""Filesystem locations for the cc-communicate upper layer (kernel + user functions).

Frozen-equivalent of scripts/lib/paths.js. The first four constants
(PLUGIN_ROOT, DATA_DIR, SESSION_CTRL_DIR, DEBUG_FILE) MUST stay in sync with
paths.js - both layers read the same session_ctrl/ folder, so they must agree
on where it is. The remaining constants are upper-layer-only.
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
MACHINE_INFO_LOG_DIR = os.path.join(DATA_DIR, 'machine_info_log')  # registered peer machines (v2.2 Amd7)

CORE_STATUS_FILE    = os.path.join(SERVER_DATA_DIR, 'core_status.json')
SESSIONS_FILE       = os.path.join(SERVER_DATA_DIR, 'sessions.json')  # persistent session registry
MACHINE_IDENTITY_FILE = os.path.join(SERVER_DATA_DIR, 'machine_identity.json')  # this machine's {type, id, claude_bin}
TERMINATE_FLAG = os.path.join(SERVER_DATA_DIR, 'terminate.flag')  # kernel_terminate signal (kernel loop checks this)


def ensure_runtime_dirs():
    """Create the runtime data directories the upper layer needs. Idempotent."""
    for d in (DATA_DIR, SESSION_CTRL_DIR, SERVER_DATA_DIR,
              QUEUE_DIR, QUEUE_RESPONSES_DIR, CONVERSATIONS_DIR,
              MACHINE_INFO_LOG_DIR):
        os.makedirs(d, exist_ok=True)

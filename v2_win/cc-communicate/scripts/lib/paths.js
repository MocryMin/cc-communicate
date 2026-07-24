'use strict';
// Resolves all cc-communicate filesystem locations.
// CLAUDE_PLUGIN_ROOT is set by Claude Code when it runs a plugin hook.
// The __dirname fallback makes every script self-locating, so code invoked by
// the skill/agent works even when that env var is not present.
const path = require('path');

const _envRoot = process.env.CLAUDE_PLUGIN_ROOT;
// Fall back to __file__-relative if CLAUDE_PLUGIN_ROOT is unset OR was left as an
// unsubstituted ${...} literal (some MCP runners don't substitute env values).
const PLUGIN_ROOT = (_envRoot && !_envRoot.includes('${')) ? _envRoot : path.join(__dirname, '..', '..');
// CC_COMMUNICATE_DATA_DIR overrides the data root (tests / custom installs, HP-11).
// SESSION_CTRL_DIR/DEBUG_FILE derive from DATA_DIR, so overriding cascades. Keep in
// sync with server/paths.py.
const _dataRootOverride = process.env.CC_COMMUNICATE_DATA_DIR;
const DATA_DIR         = _dataRootOverride ? path.resolve(_dataRootOverride) : path.join(PLUGIN_ROOT, 'data');
const SESSION_CTRL_DIR = path.join(DATA_DIR, 'session_ctrl'); // append-only event log
const DEBUG_FILE       = path.join(DATA_DIR, 'debug.log');

module.exports = { PLUGIN_ROOT, DATA_DIR, SESSION_CTRL_DIR, DEBUG_FILE };

'use strict';
// Resolves all cc-monitor filesystem locations.
// CLAUDE_PLUGIN_ROOT is set by Claude Code when it runs a plugin hook.
// The __dirname fallback makes every script self-locating, so code invoked by
// the skill/agent works even when that env var is not present.
const path = require('path');

const PLUGIN_ROOT = process.env.CLAUDE_PLUGIN_ROOT || path.join(__dirname, '..', '..');
const DATA_DIR         = path.join(PLUGIN_ROOT, 'data');
const SESSION_CTRL_DIR = path.join(DATA_DIR, 'session_ctrl'); // append-only event log
const DEBUG_FILE       = path.join(DATA_DIR, 'debug.log');

module.exports = { PLUGIN_ROOT, DATA_DIR, SESSION_CTRL_DIR, DEBUG_FILE };

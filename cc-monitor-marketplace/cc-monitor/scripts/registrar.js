#!/usr/bin/env node
'use strict';
/*
 * cc-monitor registrar — APPEND-ONLY event logger.
 * Invoked by Claude Code SessionStart / SessionEnd hooks.
 *   node registrar.js start   < (hook JSON on stdin)
 *   node registrar.js end     < (hook JSON on stdin)
 *   node registrar.js diag             (print resolved ancestor chain, no write)
 *
 * The hook NEVER reads, NEVER locks, NEVER mutates a shared table. Each
 * invocation appends ONE uniquely-named event file to data/session_ctrl/:
 *   start_<event_ts>_<session_id>.json   (carries pid, cwd, start_time, source)
 *   end_<event_ts>_<session_id>.json     (carries session_id)
 *
 * A separate kernel server (NOT implemented here) reads this folder, replays
 * events in timestamp order into an in-memory session_status table (start →
 * upsert by session_id, end → delete), and owns liveness / zombie judgement
 * (pid + start_time) — lazily, on access request. Because the table lives in
 * the server's private memory, it needs no lock; the append-only log is the
 * durable source of truth and lets the server be started/rebuilt on demand.
 *
 * Filename uniqueness: timestamp alone collides (two sessions starting in the
 * same millisecond), so session_id is appended; an exclusive `wx` create with
 * numeric retry guards the same-session-same-ms edge.
 */
const fs = require('fs');
const path = require('path');
const { resolveClaude } = require('./lib/proc');
const { SESSION_CTRL_DIR, DATA_DIR, DEBUG_FILE } = require('./lib/paths');

const MODE = (process.argv[2] || '').toLowerCase();

function dbg(msg) { try { fs.appendFileSync(DEBUG_FILE, `[${new Date().toISOString()}] ${MODE} ${msg}\n`); } catch (_) {} }
function readStdinJson() {
  let raw = '';
  try { raw = fs.readFileSync(0, 'utf8'); } catch (_) {}
  if (!raw.trim()) return {};
  try { return JSON.parse(raw); } catch (_) { return {}; }
}

// Append one event file with a guaranteed-unique name. Exclusive create (wx)
// makes concurrent writers safe: a name clash fails with EEXIST and we retry
// with a numeric suffix. No lock, no read, no mutation of any other file.
function appendEvent(type, payload) {
  try { fs.mkdirSync(SESSION_CTRL_DIR, { recursive: true }); } catch (_) {}
  const ts  = String(Date.now()).padStart(13, '0');          // fixed-width → sorts lexicographically
  const sid = (payload.session_id || 'unknown').replace(/[^A-Za-z0-9_-]/g, '_');
  const base = `${type}_${ts}_${sid}`;
  for (let i = 0; i < 1000; i++) {
    const name = i === 0 ? `${base}.json` : `${base}__${i}.json`;
    const p = path.join(SESSION_CTRL_DIR, name);
    try {
      const fd = fs.openSync(p, 'wx');      // atomic + collision-detecting
      fs.writeSync(fd, JSON.stringify(payload, null, 2));
      fs.closeSync(fd);
      return p;
    } catch (e) {
      if (e.code === 'EEXIST') continue;     // collision — next suffix
      throw e;
    }
  }
  throw new Error('could not allocate unique event filename after 1000 tries');
}

function main() {
  try { fs.mkdirSync(DATA_DIR, { recursive: true }); } catch (_) {}

  if (MODE === 'diag') {
    console.log(JSON.stringify(resolveClaude(process.pid), null, 2));
    return;
  }

  const input = readStdinJson();
  const sid = input.session_id;
  if (!sid) { dbg('no session_id in input; skipping'); return; }

  if (MODE === 'start') {
    const r = resolveClaude(process.pid);
    dbg(`start sid=${sid} pid=${r.pid} chain=${JSON.stringify(r.chain.map(c => c.pid))}`);
    const p = appendEvent('start', {
      event: 'start',
      event_ts: Date.now(),
      session_id: sid,
      pid: r.pid,
      cwd: input.cwd || process.cwd(),
      start_time: r.start,        // claude process creation time — for liveness later
      source: input.source || null,
    });
    dbg(`wrote ${path.basename(p)}`);
  } else if (MODE === 'end') {
    dbg(`end sid=${sid}`);
    const p = appendEvent('end', {
      event: 'end',
      event_ts: Date.now(),
      session_id: sid,
    });
    dbg(`wrote ${path.basename(p)}`);
  } else {
    dbg('unknown mode: ' + MODE);
  }
}

main();

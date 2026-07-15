'use strict';
// Cross-platform process introspection.
//   - Windows: PowerShell + CIM
//   - Linux:   /proc
//   - macOS:   `ps -o etime`     (best-effort)
const { execFileSync } = require('child_process');
const fs = require('fs');

// A process is "the claude binary" iff its cmdline's FIRST token (the
// executable) is/ends with `claude` or `claude.exe`. v0.1 tested the FULL
// cmdline for "claude" and excluded cmdlines containing "cc-communicate" - but
// spawn/evoke prompts contain "cc-communicate", so a spawned CC's claude parent
// was rejected and the hook fell back to a fragile ppid guess (v2.2 Amd1 /
// BUG-1). Checking only the first token avoids the prompt text: our scripts run
// as `python ...`/`node ...`, shells as `cmd`/`bash`/`tmux` - none have claude
// as the first token.
function isClaudeCmd(cmd) {
  if (!cmd) return false;
  const first = cmd.split(/\s+/)[0];
  return /(^|\/|\\)claude(\.exe)?$/i.test(first);
}

/* ---- Windows ---------------------------------------------------------- */
function procTableWindows() {
  const ps =
    "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine," +
    "@{N='Created';E={ if ($_.CreationDate) { $_.CreationDate.ToString('o') } else { '' } }} | " +
    "ConvertTo-Json -Compress";
  const out = execFileSync('powershell',
    ['-NoProfile', '-NonInteractive', '-Command', ps],
    { encoding: 'utf8', maxBuffer: 128 * 1024 * 1024, windowsHide: true });
  let arr = JSON.parse(out);
  if (!Array.isArray(arr)) arr = [arr];
  const map = new Map();
  for (const p of arr) {
    map.set(p.ProcessId, { ppid: p.ParentProcessId, cmd: p.CommandLine || '', start: p.Created || null });
  }
  return map;
}

/* ---- Linux (/proc) ---------------------------------------------------- */
function procTableLinux() {
  const map = new Map();
  let btime = 0;
  try {
    const stat = fs.readFileSync('/proc/stat', 'utf8');
    const m = stat.match(/btime\s+(\d+)/);
    if (m) btime = parseInt(m[1], 10);
  } catch (_) {}
  const hz = 100;
  for (const entry of fs.readdirSync('/proc')) {
    if (!/^\d+$/.test(entry)) continue;
    const pid = parseInt(entry, 10);
    let ppid = 0, cmd = '', starttime = 0;
    try {
      const s = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
      const closeParen = s.lastIndexOf(')');
      const pre = s.slice(0, closeParen + 1);
      const rest = s.slice(closeParen + 2).split(/\s+/);
      const cmdMatch = pre.match(/\((.*)\)$/);
      cmd = cmdMatch ? cmdMatch[1] : '';
      ppid = parseInt(rest[1], 10);
      starttime = parseInt(rest[19], 10);
    } catch (_) { continue; }
    try { cmd = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').replace(/\0/g, ' ').trim() || cmd; } catch (_) {}
    const start = (btime && starttime) ? new Date((btime + starttime / hz) * 1000).toISOString() : null;
    map.set(pid, { ppid, cmd, start });
  }
  return map;
}

/* ---- macOS (ps) ------------------------------------------------------- */
function parseEtimeToSec(e) {
  const m = e.trim().match(/^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$/);
  if (!m) return null;
  const d = m[1] ? +m[1] : 0, h = m[2] ? +m[2] : 0, mi = +m[3], s = +m[4];
  return d * 86400 + h * 3600 + mi * 60 + s;
}
function procTableMac() {
  const out = execFileSync('ps', ['-eo', 'pid=,ppid=,etime=,command='], { encoding: 'utf8' });
  const map = new Map();
  const now = Date.now();
  for (const line of out.split('\n')) {
    if (!line.trim()) continue;
    const m = line.trim().match(/^(\d+)\s+(\d+)\s+(\S+)\s+(.*)$/);
    if (!m) continue;
    const pid = +m[1], ppid = +m[2], etime = m[3], cmd = m[4];
    const sec = parseEtimeToSec(etime);
    const start = sec != null ? new Date(now - sec * 1000).toISOString() : null;
    map.set(pid, { ppid, cmd, start });
  }
  return map;
}

function getProcTable() {
  if (process.platform === 'win32') return procTableWindows();
  if (process.platform === 'linux')  return procTableLinux();
  return procTableMac();
}

// Walk up from selfPid; return the nearest ancestor that is the claude binary.
function resolveClaude(selfPid) {
  let map;
  try { map = getProcTable(); }
  catch (e) { return { pid: process.ppid, start: null, chain: [], error: e.message }; }

  const start = selfPid || process.pid;
  const chain = [];
  const seen = new Set();
  let cur = start, guard = 0;
  while (cur && map.has(cur) && guard++ < 64 && !seen.has(cur)) {
    seen.add(cur);
    const info = map.get(cur);
    chain.push({ pid: cur, cmd: (info.cmd || '').slice(0, 160) });
    if (cur !== start && isClaudeCmd(info.cmd)) {
      return { pid: cur, start: info.start, chain };
    }
    cur = info.ppid;
  }
  const fb = map.get(process.pid) ? map.get(process.pid).ppid : process.ppid;
  return { pid: fb, start: map.has(fb) ? map.get(fb).start : null, chain };
}

module.exports = { getProcTable, resolveClaude, liveProcs, isClaudeCmd };

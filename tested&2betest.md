# V2 Test Record (2026-07-15)

Two sections: **§1 Tested** (what I ran, method, result, confidence) and
**§2 To-be-tested** (what full functional verification still needs, method,
expected result). Also see `log/implementation-log.md` for raw output.

---

## §1 Tested (by me, this session)

### T1 — Python syntax (all server modules, both sides)
- **Method**: `ast.parse` every `*.py` in `v2_win/cc-communicate/server` and
  `v2_wsl/cc-communicate/server`, with `SyntaxWarning` promoted to error.
- **Content**: catch syntax errors + invalid escape sequences.
- **Result**: OK, 0 errors, no warnings (after fixing one `C:\ ` docstring escape
  in machine_sign_up.py -> `C:/`).
- **Confidence**: high.

### T2 — Imports + BUG-1 (resolve_claude name-based) — Windows
- **Method**: `python -c` from `v2_win/cc-communicate/server`; `sys.path.insert`;
  import paths/proc/machine_identity/conversations/spawn/check_core; call
  `resolve_claude(os.getpid())`.
- **Result**: imports OK; `resolve_claude` found claude.exe ancestor **pid 9600**
  (the same process from the earlier inspection) with start_time. `detect_type`
  = `win-host`. `claude_binary_path` = full claude.exe path.
- **Confidence**: high — BUG-1 root cause (cmdline skip-list rejecting the
  cc-communicate prompt) is fixed; name-based matching finds the binary.

### T3 — BUG-5 dynamic path conversion — Windows
- **Method**: `machine_identity.to_peer_perspective(paths.DATA_DIR, 'wsl-ubuntu')`.
- **Result**: `C:\研究生\...\data` -> `/mnt/c/研究生/...\data` (drive letter
  derived from the path, not hardcoded `c`; backslashes -> forward).
- **Confidence**: high — portable across drives.

### T4 — Kernel lazy-start + queue RPC + machine_identity — Windows
- **Method**: `rpc_client.call('query_session', {sid:'x'}, timeout=30)` (triggers
  `ensure_core` -> kernel spawn -> dispatch), then `check_alive`, then
  `kernel_terminate`; inspect `core_status.json` + `kernel.log` + `kernel.stderr.log`.
- **Result**: `query_session` -> None; `check_alive` -> 0;
  machine_identity.json generated `{type:win-host, id:..., claude_bin:None}`;
  kernel.log shows "READY"; stderr empty (no import errors). First run:
  kernel_terminate did NOT stop the kernel (__main__ bug) -> force-killed.
  After fix (T7): kernel_terminate -> status=0, log "kernel exiting"/"kernel
  exited", no lingering process.
- **Confidence**: high for the local RPC lifecycle.

### T5 — listen.py local detect+archive+print — Windows
- **Method**: create a fake conversation `data/conversations/<a>__<b>/pipe/`
  with a message file (from a to b); run `python server/listen.py <b> 5`.
- **Result**: stdout = `[{"time":..., "from_id":"a...", "message":"hello reply
  from a"}]`, exit 0; the pipe file moved to `log/`. (No kernel involved — pure
  file I/O, as designed #W5.)
- **Confidence**: high for the local listen path.

### T6 — WSL-side imports + detect_type + path conversion (v2_wsl under WSL)
- **Method**: Wrote a UTF-8 test script (to avoid CLI Chinese-encoding issues)
  that imports ALL v2_wsl server modules under `wsl.exe -d Ubuntu -- python3`;
  called `detect_type` + `to_peer_perspective(DATA_DIR, 'win-host')`. (Needed
  `MSYS_NO_PATHCONV=1` on the wsl.exe invocation — C2 confirmed.)
- **Result**: imports OK (all modules); `detect_type` = `wsl-ubuntu`;
  `to_peer_perspective` = `//wsl.localhost/Ubuntu/mnt/c/.../data` (distro from
  WSL_DISTRO_NAME, dynamic). `claude_binary_path` = None (expected — the test
  python3 has no WSL claude ancestor; a real WSL CC would).
- **Confidence**: high that the WSL build imports and type-detects correctly;
  the wsl->host conversion is symmetric to T3.

### T7 — kernel_terminate fix (flag-file)
- **Method**: after the T4 fix, re-ran T4's sequence on a clean data dir.
- **Result**: `kernel_terminate` -> core_status status=0; kernel.log shows
  "kernel exiting"+"kernel exited"; no lingering python.exe.
- **Confidence**: high.

### T8 — v2_win vs v2_wsl parity
- **Method**: `diff -rq -x data -x __pycache__ v2_win v2_wsl`.
- **Result**: only `.mcp.json` differs (`python` vs `python3`). As designed.
- **Confidence**: high.

### T9 — WSL deps
- **Method**: `wsl.exe -d Ubuntu -- python3 -c "import psutil, filelock, mcp; ..."`
  + check `/proc/version` for `microsoft` + `WSL_DISTRO_NAME`.
- **Result**: deps OK; `is_wsl=True`; `WSL_DISTRO_NAME=Ubuntu`.
- **Confidence**: high.

### T10 - JS-hook layer live (proc.js: liveProcs + isClaudeCmd quote bugs)
- **Bugs found (live, after install)**: installing v2_win + `my_session_id`
  always returned `"failed, no session recorded for claude pid <N>"`. Root cause
  was NOT B1 (`--resume` not firing) - the SessionStart hook WAS firing, but
  `registrar.js` crashed on `require('./lib/proc')`:
  1. `proc.js` exported `liveProcs` (never defined) -> `ReferenceError` on module
     load -> `registrar.js` died before writing anything -> no session ever
     registered (any OS, any start mode).
  2. `isClaudeCmd` did `cmd.split(/\s+/)[0]` + basename regex, but Windows CIM
     reports the exe as a QUOTED path (`"…\claude.exe"`); the trailing `"` broke
     the regex -> `resolveClaude` skipped the real claude and fell back to a
     shell ancestor (wrong pid). Windows-only manifestation; WSL
     `/proc/<pid>/cmdline` is unquoted so it wouldn't manifest there, but the fix
     is defensive.
- **v2_wsl applicability**: bug 1 is OS-independent (pure JS) -> must affect
  v2_wsl too, fixed. Bug 2 is Windows-manifestation -> wouldn't fire on WSL, but
  fix is in place (harmless). Verified: v2_wsl `registrar.js diag` returns the
  real claude pid; v2_wsl `proc.js` byte-identical to v2_win's.
- **Fix**: commit `1e03f21` - dropped `liveProcs` from exports (only
  `resolveClaude` is imported); `isClaudeCmd` extracts the first token respecting
  quotes before testing. Applied to BOTH v2_win and v2_wsl `proc.js`.
- **Method**: `node registrar.js diag` (v2_win + v2_wsl) -> real claude pid (was
  a bash shell pid); `echo {session_id:TEST_DIAG_SID,...} | node registrar.js
  start` -> writes `start_<ts>_TEST_DIAG_SID.json` with correct claude pid +
  start_time; then `my_session_id` (MCP) -> returns `TEST_DIAG_SID` (kernel
  replayed the event, resolved pid->sid). Test event cleaned up after.
- **Result**: full identity chain verified live (JS hook -> kernel ->
  `my_session_id`) on Windows; v2_wsl `registrar.js diag` also returns the real
  claude pid.
- **Confidence**: high that session registration now works on Windows. The
  `--resume`-fires-SessionStart question (B1) was being MASKED by this crash and
  is now actually testable.
- **Test gap**: T1-T9 exercised the Python side only; the JS hook
  (`registrar.js`/`proc.js`) was never executed live - that's how both bugs
  slipped. JS-hook execution is now covered.

---

## §2 To-be-tested (need user / WSL deployment)

These need real CC spawning and/or a deployed WSL side. I can't fully run them
without risking stray CC processes, trust prompts, or needing two live CCs.

### B1 — #1 Hook on WSL (CRITICAL — make-or-break)
- **What**: does SessionStart/End land in `data/session_ctrl/` for a WSL CC, in
  three scenarios: (a) manually-started CC, (b) `.py`/tmux-spawned CC, (c)
  `claude --resume <sid>`.
- **Method**: deploy v2_wsl into WSL ext4; install plugin in a WSL CC; start a
  CC each way; check `data/session_ctrl/` for `start_<ts>_<sid>.json` (with
  correct pid) and `end_...json`.
- **Expected**: events land in all three. **Critical sub-question**: does
  `--resume` fire SessionStart? If NOT, evoke/connect-to-dead is broken (the
  kernel never learns the resumed CC's new pid -> check_alive stays 0 ->
  connect times out). Borrow the running `claude -r` (pid 19588 on host) or
  spawn one in WSL to test.
- **Who**: me (after v2_wsl deployed) + user may need to grant trust / interact.
- **Update (T10)**: the hook WAS firing all along - `registrar.js` was crashing
  on `require('./lib/proc')` (liveProcs) so no event landed, mimicking "hook
  didn't fire". After T10 the Windows hook records correctly; the
  `--resume`-fires-SessionStart sub-question is now testable (restart CC with
  `--resume`, call `my_session_id` -> a real sid means --resume fires
  SessionStart). WSL scenarios still need v2_wsl deployed.

### B2 — #6 Trust dialog skip
- **What**: does `--dangerously-skip-permissions` let a spawned CC start without
  the workspace-trust prompt blocking the prompt?
- **Method**: `spawn_cc_new`/`spawn_cc_resume` (or manual `claude
  --dangerously-skip-permissions <prompt>`); observe whether the CC reaches the
  REPL and processes the prompt.
- **Expected**: no trust dialog; CC enters REPL and runs the prompt.
- **Who**: me or user (spawns a real CC).

### B3 — BUG-1 end-to-end
- **What**: a spawned/evoked CC (whose prompt contains "cc-communicate") can call
  `my_session_id` and get its sid.
- **Method**: after B2 unblocks spawning, spawn a CC with the evoke prompt; have
  it call `my_session_id`.
- **Expected**: returns a sid (was "failed, could not find claude ancestor"
  pre-fix).
- **Who**: me (after B2).

### B4 — connect end-to-end (single machine)
- **What**: two CCs on the same machine: connect -> hello -> reply -> succeed.
- **Method**: two live CCs (both with plugin); `my_session_id` each; `connect`;
  the target listens + replies.
- **Expected**: "connect succeed; reply: ...".
- **Who**: user (drives two CCs) or me driving both.

### B5 — Cross-realm e2e (Phase 2) + remote wake
- **What**: WSL CC ↔ host CC connect/send/listen/close; + remote-wake.
- **Method**: deploy v2_wsl in WSL; run `machine_add.py` (host) +
  `machine_sign_up.py` (WSL); WSL CC connects to host CC (and reverse); then
  kill host kernel, WSL CC connects again -> host kernel should wake (Amd8).
- **Expected**: cross-realm connect succeeds; remote-wake restarts the host
  kernel (core_status goes 0 -> 1).
- **Who**: user + me.

### B6 — 9p dir-change visibility
- **What**: latency for a host-written file to appear in WSL `os.listdir(/mnt/c/)`.
- **Method**: host writes a file; WSL polls listdir; measure time-to-visible.
- **Expected**: < the listen.py poll window (2s) + settle (3s); if larger,
  listen.py cross-realm detection may lag -> adjust.
- **Who**: me (host write + WSL poll).

### B7 — handshake round-trip
- **What**: `machine_add` (host) + `machine_sign_up` (WSL) complete and both
  `machine_info_log/` get entries with correct data_dir/wake fields.
- **Method**: run both scripts; inspect `data/machine_info_log/*.json` on both
  sides; verify `data_dir` is the peer's perspective and `wake_script_native`
  is peer-native.
- **Expected**: both sides registered; fields correct.
- **Who**: me (after v2_wsl deployed).

---

## §3 Confidence summary

| Area | Confidence | Reason |
|---|---|---|
| Code correctness (logic, imports, syntax) | high | T1/T2/T6 + code review |
| BUG-1, BUG-5 fixes | high | T2/T3/T6 |
| Local kernel + RPC lifecycle | high | T4/T7 |
| listen.py local path | high | T5 |
| connect end-to-end | LOW | not run (needs real CC reply) — B4 |
| cross-realm (call_remote, wake, handshake) | MEDIUM | code reviewed, WSL->host wake channel verified feasible, but no end-to-end — B5/B7 |
| JS hook (registrar.js/proc.js) | high | T10 - liveProcs + isClaudeCmd quote bugs fixed; live chain verified |
| `--resume` SessionStart (#1) | UNKNOWN (now testable) | T10 unmasked: hook fires, was crashing; --resume Q still open - B1 |
| trust flag (#6) | UNKNOWN | unverified — B2 |

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

### T11 - Live end-to-end on Windows host (B1 + cross-session discovery)
- **Context**: after the T10 fix, installed v2_win, restarted CC with
  `claude --resume`; a SECOND live CC (38e1e965, "GPU trail" dir) was also
  registered. Two real CCs on one host.
- **Method**: `my_session_id`; inspected `data/session_ctrl/` + `debug.log`;
  `query_session(38e1e965)`; `check_alive(38e1e965)`.
- **Result**:
  - `my_session_id` -> `81e4c033-...` (real sid). `debug.log` shows the hook
    fired on restart with `source=resume` -> **B1 CONFIRMED on Windows:
    `claude --resume` fires SessionStart**. SessionEnd also fired (end event
    before the resume-start).
  - `query_session(38e1e965)` -> full info (pid 38404, cwd, start_time, machine
    win-host). Cross-session discovery works.
  - `check_alive(38e1e965)` -> 1 (pid + start_time verified). Liveness works.
- **Cleanup note**: a fake `TEST_DIAG_SID` from T10's manual test had persisted
  in `sessions.json` (kernel loads it on startup and only adds/updates from
  session_ctrl, never removes unbacked entries). Fixed by stopping the kernel
  (TERMINATE_FLAG), deleting `sessions.json`, letting it rebuild from
  session_ctrl -> TEST_DIAG_SID gone. Not a real bug (only happens from manual
  event-file deletion; real sessions end via SessionEnd events).
- **Confidence**: high for Windows-host identity + discovery + liveness.

### T12 - create_collaborator live: _archive_reply crash + prompt + kernel robustness (3 bugs)
- **Context**: tested `create_collaborator(81e4c033, <project cwd>, hold_time=180)`
  live. It spawned a NEW CC (45e9bb6e) in a new window with
  `--dangerously-skip-permissions` + the collaborator prompt. The new CC
  registered (start event 15:47:57), `find_new_session` found it, `connect`
  sent the hello - the new CC's `listen.py` received + archived it (file in
  `log/`). But `create_collaborator` returned
  `TypeError: '<' not supported between instances of 'NoneType' and 'str'`.
- **Bugs found (live)**:
  1. **`_archive_reply` dead-code crash** (CRITICAL): the local branch computed
     `conv_name = os.path.basename(conversations.conv_dir(caller, None))` -
     `conv_name` was NEVER used, but `conv_dir` does `sorted([sid_a, sid_b])`
     and `sorted([str, None])` raises exactly the TypeError above. So the moment
     `connect`'s `_poll_reply` found the reply and tried to archive it, it
     crashed - the connection was actually ESTABLISHED (reply received) but
     connect could never return success. Reproduced in isolation:
     `conv_dir('81e4...', None)` -> identical TypeError.
  2. **Prompt ambiguity**: the collaborator prompt + connect hello said "reply
     to any hello" / "reply immediately with any message" without saying HOW.
     The spawned CC guessed `connect(...)` instead of `send_message(...)`. Its
     connect-hello landed in my pipe and was what `_poll_reply` matched as the
     reply (triggering bug 1). It also blocked 300s then `_withdraw` deleted its
     own hello (why `pipe/` was empty on inspection).
  3. **kernel `drain_queue` PermissionError crash**: kernel.log showed the
     kernel crashed mid-request with `PermissionError: [Errno 13]` reading a
     queue file (Windows transient - AV scan / write race). `_read_json` only
     caught FileNotFoundError/JSONDecodeError, so PermissionError propagated
     and killed the kernel (it self-restarted via ensure_core on next RPC).
- **v2_wsl applicability**: all 3 are in shared code (user_functions.py,
  kernel_api.py, kernel.py) -> affect v2_wsl identically. Bug 3's
  PermissionError is a Windows manifestation (Linux file races differ) but the
  guard is harmless there. Applied all fixes to BOTH v2_win and v2_wsl (cp'd
  the 3 files; parity re-verified by diff).
- **Fix**:
  1. removed the dead `conv_name` line in `_archive_reply` (log_dir is derived
     from the pipe path directly, no conv_dir needed).
  2. connect hello + create_collaborator prompt + evoke prompt now explicitly
     say to reply with `send_message(your_id, peer_id, <message>)` (and the
     collaborator prompt says "do NOT call connect to reply").
  3. `drain_queue` wraps `_read_json(path)` in `except OSError: continue` (skip
     the file, retry next cycle) instead of crashing.
- **Method**: repro `_archive_reply(None, sid, fname, real_pipe_path)` -> before
  fix: TypeError; after fix: pipe file archived to log/ cleanly (verified).
  `py_compile` on all 6 files (3 v2_win + 3 v2_wsl) OK. v2_win<->v2_wsl parity
  diff clean.
- **Result**: `_archive_reply` no longer crashes (verified). Bonus from the live
  attempt: **B2 partially confirmed** - the spawned CC started with
  `--dangerously-skip-permissions`, reached the REPL, and processed the prompt
  (called my_session_id + listen) with NO trust-dialog block -> the flag works.
  **B3 confirmed** - the spawned CC 45e9bb6e called `my_session_id` and got its
  sid. The spawn->register->find->connect-hello->listen-receive chain all works
  live; only the reply-archive step was broken (now fixed).
- **Confidence**: high for the `_archive_reply` fix (reproduced+fixed+verified).
  create_collaborator end-to-end still needs a clean re-run after the MCP server
  reloads the fixed code (the running MCP server process has the old
  user_functions.py cached). Stray CC 45e9bb6e left running (its connect timed
  out; that window can be closed).

### T13 - create_collaborator end-to-end SUCCESS (fix verified live) + B4 confirmed
- **Context**: re-ran create_collaborator after the T12 fix, via a direct Python
  script importing the fixed `user_functions` fresh (the running MCP server still
  had the old code cached; a script bypasses that). caller=81e4c033,
  cwd=project, hold_time=120.
- **Method**: script calls `user_functions.create_collaborator(...)`; it spawns a
  new CC (a1e02819, pid 18132), `find_new_session` finds it, `connect` sends
  hello, polls for the reply.
- **Result**: `connect succeed; reply: Hello! I received your connect hello.
  Channel established. My session ID is a1e02819-... Ready to collaborate - what
  would you like to work on?`
  - The hello (81e4c033 -> a1e02819) was delivered + archived by a1e02819's
    listen.py (in `log/`).
  - a1e02819 replied via **send_message** (a1e02819 -> 81e4c033) - the improved
    prompt worked (the earlier 45e9bb6e had wrongly used connect).
  - The FIXED `_archive_reply` archived the reply to `log/` cleanly - NO crash.
    `connect` returned success.
- **Confirms**:
  - **create_collaborator works** (spawn + find + connect + reply).
  - **B2** (trust flag): a1e02819 reached the REPL with
    `--dangerously-skip-permissions`, no trust dialog.
  - **B3** (spawned CC my_session_id): a1e02819 knew its sid.
  - **B4** (connect end-to-end): hello -> reply -> succeed, single machine.
  - All 3 T12 fixes validated end-to-end.
- **Caveat**: the re-test was via script, not the MCP tool (the MCP server
  process caches the old `user_functions.py`). For future MCP-tool
  create_collaborator calls to use the fix, the MCP server must reload
  (`/reload-plugins` or CC restart) - which previously disrupted session
  tracking, so it was avoided here.
- **Leftover state**: two stray CCs from earlier attempts - 45e9bb6e (old, idle,
  its connect timed out) and a1e02819 (this test, alive + connected to
  81e4c033). Close 45e9bb6e's window; a1e02819 is a usable collaborator.
- **Confidence**: high - create_collaborator + connect end-to-end verified live.

### T14 - rpc_client _consume_response PermissionError (local RPC crash)
- **Context**: retrying `connect(81e4c033, 5227028e)` after a create_collaborator
  timeout; the connect's `check_alive` RPC hit a transient Windows
  `PermissionError` reading the response file, crashing the whole connect call
  (`Error executing tool connect: [Errno 13] Permission denied:
  data/queue/responses/<rid>.json`).
- **Bug**: `rpc_client._consume_response` (the LOCAL RPC path) caught only
  `(FileNotFoundError, json.JSONDecodeError)` - NOT `OSError`/`PermissionError`.
  So a transient AV-scan / write-race `PermissionError` on
  `data/queue/responses/<rid>.json` propagated and killed the call. Inconsistent
  with `_consume_remote` (the remote path), which already caught `OSError`. Same
  class as the kernel `drain_queue` Bug 3 (T12).
- **Fix**: add `OSError` to `_consume_response`'s except (returns None -> the
  poll loop retries next cycle), mirroring `_consume_remote`.
- **Files**: `rpc_client.py` (v2_win + v2_wsl, parity verified).
- **v2_wsl applicability**: yes - same `rpc_client.py`; WSL is less AV-prone but
  the write-race can still occur.
- **Method**: reproduced (the connect error showed the exact path); fix verified
  by py_compile + parity + logic review.
- **Confidence**: high (clear inconsistency; the remote path already had it).

### T15 - create_collaborator hold_time race + _poll_reply no final scan
- **Context**: `create_collaborator` via the MCP tool (hold_time=120) spawned
  5227028e. The CC replied at ts 1784105863935 = **120.95s after registering**
  (start 1784105742984) - just past the 120s hold_time. `_poll_reply`'s deadline
  (~register + 1-2s overhead + 120s) missed the reply by <1s; its 0.5s poll +
  **no final scan** after the loop meant the last-window reply was lost.
  `connect` returned "timeout"; `_withdraw` ran; the CC's (race-winning)
  `send_message` then landed in a withdrawn conv. A retry connect repeated the
  race; the CC's later reply hit "connection not registered" (conv withdrawn).
- **Root cause**: NOT a logic bug - an extremely tight timing race at the
  hold_time boundary. The CC cold-start (boot + tool load + listen + reply)
  takes ~121s on Windows; 120s hold_time is too short. (On WSL the cold-start is
  faster, so the WSL-only create_collaborator test succeeded - see WSL report.)
- **The CC behaved correctly**: my_session_id -> listen -> run listener
  (listen.py pid 16200) -> receive hello -> reply via **send_message** (NOT
  connect). The T12 prompt fix is confirmed working through the actual MCP tool.
  B2/B3/B4 re-confirmed via the MCP path.
- **Fix 1 (user request)**: `_MIN_HOLD_TIME = 300` floor in create_collaborator
  - `hold_time = max(hold_time, _MIN_HOLD_TIME)`. Prevents anyone overriding
  below the cold-start budget. (Default stays 300 == the floor.)
- **Fix 2 (robustness)**: `_poll_reply` refactored into a `_claim_reply` helper
  + a **final scan** after the deadline, so a reply landing in the last 0.5s
  poll window isn't missed.
- **Note**: the CC ran listen.py with shell redirection (`> /tmp/log 2>&1 &`) +
  manual `cat`-poll instead of `Bash(run_in_background=true)` task-notification.
  Worked, but adds latency; the prompt's "run in the background" is ambiguous.
  Not a code bug; possible prompt refinement later.
- **Files**: `user_functions.py` (v2_win + v2_wsl, parity verified).
- **v2_wsl applicability**: yes - same `user_functions.py`; the floor + final
  scan protect WSL too (even though WSL already succeeded).
- **Method**: analyzed the 5227028e transcript (reply ts vs start = 120.95s vs
  hold_time 120s); read `_poll_reply` (confirmed 0.5s poll, no final scan); fix
  verified by py_compile + parity + logic sanity (max clamp + final-scan path).
- **Confidence**: high for the fix; a clean MCP-tool re-test (hold_time
  auto-floored to 300) pending plugin reload.

### T16 - machine_identity stale-type cache (deployment artifact, blocks B5)
- **Context**: the WSL-only test report flagged `machine_identity` "Cached as
  win-host; detect_type() correctly returns wsl-ubuntu - deployment artifact".
  v2_wsl's `data/server/machine_identity.json` was copied from v2_win
  (type=win-host) and `load_or_create()` trusted the cached type without
  re-validating against `detect_type()`.
- **Bug**: `load_or_create()` only regenerated when `type`/`id` fields were
  MISSING, not when the cached `type` was WRONG. So a data dir copied across
  realms keeps the wrong machine type -> cross-realm routing/handshake would
  misidentify the WSL peer as win-host. Blocks B5/B7.
- **Fix**: `load_or_create()` now compares the cached type to `detect_type()`; on
  mismatch it regenerates type + id (a mismatch means the data dir came from a
  different machine/realm, so a new id is correct). Also deleted the stale
  v2_wsl `machine_identity.json` so it regenerates as wsl-ubuntu on next WSL CC
  start.
- **Files**: `machine_identity.py` (v2_win + v2_wsl, parity verified) + deleted
  v2_wsl `data/server/machine_identity.json`.
- **v2_wsl applicability**: this IS the v2_wsl fix; v2_win gets the same
  robustness (a win-host cache on the actual host matches detect_type(), so no
  spurious regen).
- **Method**: read `machine_identity.py` (confirmed `load_or_create` trusted the
  cached type); fix verified by py_compile + parity + logic sanity (win-host
  cache -> REGENERATE, wsl-ubuntu cache -> KEEP).
- **Confidence**: high; the WSL CC must reload (MCP restart) to pick up the fix
  + regenerate its identity as wsl-ubuntu.

### T17 - C3 ts-filter: stale close-notice / self-connect read as reply
- **Bug**: `_claim_reply`/`_poll_reply` accepted ANY pipe message from the target
  as the reply. If a prior `close_connection(B,A)` left a `[CONNECTION CLOSED by
  B]` notice in A's pipe (A wasn't listening when it arrived), a later
  `connect(A,B)` would read that stale notice as B's reply -> **false success**
  (B never replied). Also `connect(A,A)` read its own hello as the reply.
- **Fix**: `connect` parses the hello's ts from `_send`'s `"message_sent at <ts>"`
  return; `_claim_reply` skips messages with `ts <= hello_ts` (the hello and any
  prior close notice predate the hello, so they're filtered).
- **Method**: unit test - fabricated stale (ts=1000) + fresh (ts=3000) pipe files
  with hello_ts=2000 -> stale skipped + left in pipe, fresh returned; self-connect
  hello (ts==hello_ts) rejected (None).
- **Confidence**: high (logic + unit test). Pending live re-verification.

### T18 - C2 blocking `listen` (listen-returns-a-command failure mode)
- **Bug (from 2 real-scene tests)**: the `listen` tool returned a shell command
  for the CC to run via Bash. The CC fumbled it every way: had to manually add
  `MSYS_NO_PATHCONV=1` (git-bash mangled the backslash paths), the background
  `listen.py` crashed (exit 1 - see T19), 5 stray `python.exe` processes
  accumulated, and the CC went off-script writing a custom `/tmp/cc_listen_loop.sh`
  bash loop instead of re-arming via the tool. The one-shot `listen.py` also meant
  the CC stopped listening after one delivery -> the keep-listen law was
  unenforceable (the collaborator never received the 2nd question).
- **Fix**: `listen` is now a BLOCKING MCP tool - it runs the poll inside the MCP
  server (`listen.listen_blocking`) and returns the messages list (or `[]` on
  timeout). The CC calls it in a loop until `close_connection`. No bash, no
  background process, no strays. The "wake" = the tool returning. The
  `create_collaborator`/`evoke` prompts + `connect`/`listen`/`close_connection`
  tool descriptions now carry the keep-listen law + an explicit anti-bash-loop
  rule ("never invoke listen.py directly, never write a shell listener").
- **Method**: unit test (`listen_blocking` returns messages addressed to sid;
  `[]` on timeout). py_compile + parity (v2_win==v2_wsl except .mcp.json).
- **Confidence**: high for the mechanism. Pending live verification; gate = does
  Claude Code tolerate a ~30s blocking MCP tool call (very likely yes - the per-
  call default is 30s).

### T19 - C5 listen.py exit-1 UTF-8 crash
- **Bug (from real-scene test 1)**: background `listen.py` exited 1 (crash). Root
  cause: `print(json.dumps(messages, ensure_ascii=False))` on a non-UTF-8 stdout
  pipe (cp936/cp1252 on Chinese Windows) raised `UnicodeEncodeError` when a
  message held non-ASCII. (Foreground re-run had no message to print -> clean
  exit 2, matching the observed pattern.) Also `_archive_local`'s read only caught
  `OSError`, not `UnicodeDecodeError` (a malformed pipe file -> crash).
- **Fix**: `listen.py` `main()` reconfigures stdout to UTF-8
  (`sys.stdout.reconfigure(encoding="utf-8")`); `_archive_local`, `_claim_reply`,
  and `collect_messages` catch `UnicodeDecodeError` alongside `OSError` (skip
  malformed files).
- **Method**: unit test - a non-UTF-8 pipe file is skipped, the good message
  after it is returned, no crash.
- **Confidence**: high (matches the exit-1-background / exit-2-foreground
  pattern). Pending live confirmation.

### T20 - C1 non-blocking best-effort `close_connection`
- **Issue**: `close_connection` made up to 3 blocking remote calls (`_collect`,
  `_send`, `_unregister`), each a `call_remote` that can block 10s+ on a dead
  peer kernel -> terminate blocked 30s+, and a failure could make the caller
  retry (wasting tokens). Violated the intended "terminate is simple, non-blocking,
  returns success, caller exits" model.
- **Fix**: `close_connection` is now best-effort + non-blocking. Remote notice +
  unregister are fire-and-forget via new `rpc_client.submit_remote_noblock`
  (submits the request without polling the response); the local path uses fast
  kernel RPCs (and drains pending for the caller). Wrapped in try/except, always
  returns `{closed: True}`, never raises. The peer's listener (kept alive per the
  listen loop) sees the notice and frees itself - no ack needed.
- **Method**: py_compile + logic review + parity. (Live test pending.)
- **Confidence**: high (logic is simple).

### T21 - R2 persist `alive_conversations` across kernel restart
- **Bug**: `alive_conversations` was in-memory only -> a kernel restart (crash /
  idle-timeout exit / terminate) dropped ALL conversation registrations ->
  subsequent `send_message` returned `failed, connection not registered` for every
  active conversation.
- **Fix**: kernel persists `alive_conversations` to `alive_conversations.json`
  (list of `[a,b,info]`; tuple keys aren't JSON-serializable). `_load_alive_convs`
  on startup, `_save_alive_convs` after `drain_queue` (when the queue was busy)
  and on exit.
- **Method**: unit test - round-trip 2 convs (save -> clear -> load -> equal);
  empty round-trip. py_compile + parity.
- **Confidence**: high (logic + unit test). Pending live restart test.

### T22 - C4 handshake guide + `help_connect_machines`
- **Gap**: a fresh WSL install cannot discover the host (no auto-discovery);
  cross-realm silently fails (`target session not exists`) until the manual
  handshake is run, with no guidance for the CC.
- **Fix**: added `server/handshake_guide.md` (playbook: clarify prerequisites,
  identify side, drive BOTH scripts via cross-realm exec - like `_wake_remote` -
  with the git-bash path-mangling caveat baked in, verify via `query_machines`,
  diagnose failures) + `help_connect_machines` MCP tool that reads + returns it.
  The CC calls the tool on "help me connect machines" prompts and follows the
  guide, asking the user clarifications and orchestrating both sides itself.
- **Method**: guide files present + identical in both trees (66 lines);
  `help_connect_machines` reads + returns the guide. (Live orchestration test
  pending.)
- **Confidence**: medium (design sound; cross-realm exec feasibility already
  proven by Amd8 wake; the guide's steps + path-mangling caveat need a live run).

### T23 - LIVE verification of C1/C2/C4/C5/R2 (real MCP tools, fresh kernel)
- **Context**: after `/reload-plugins` (win+WSL) the MCP server restarted with new
  code; the kernel was restarted via `terminate.flag` so R2 / C5-kernel_api /
  C2-evoke-prompt were live. Drove the REAL MCP tools from a live CC session
  (sid 81e4c033) with a synthetic peer (cctest-peer-0001) holding the conv.
- **C2 blocking listen - CONFIRMED**: `listen(sid, timeout=8)` blocked ~8s then
  returned `[]` (listen_blocking polls every 2s to deadline, then returns []);
  a later call returned B's unicode reply within the timeout. Claude Code
  tolerates the blocking MCP call - the gate PASSED (no kill, no crash, clean
  return). The "wake" = the tool returning; re-arm = call again.
- **C5 UTF-8 / no exit-1 - CONFIRMED**: `listen.py cctest-peer-0001 5` printed
  `你好 from A ✓ ... こんにちは` as JSON, exit 0 (NO exit-1). A unicode reply was
  also delivered through the real MCP `listen`. stdout.reconfigure(utf-8) holds on
  Chinese Windows.
- **R2 conv persists across kernel restart - CONFIRMED**: registered conv (A,B) ->
  `alive_conversations.json` written; touched `terminate.flag` (kernel exited
  cleanly: status=0, saved state); the next `send_message` restarted the kernel via
  `ensure_core` and SUCCEEDED without re-registering. kernel.log shows
  `loaded alive_conversations.json: 1 convs`. Before R2 this returned
  `failed, connection not registered`.
- **C1 non-blocking close - CONFIRMED**: `close_connection(A,B)` returned
  `{closed:true, delivered_pending:[]}` immediately; B's next listen received the
  `[CONNECTION CLOSED by A]` notice; `alive_conversations.json` became `[]`
  (unregistered). Non-blocking, always succeeds.
- **C4 help_connect_machines - CONFIRMED**: tool returns the full guide markdown.
  (Full cross-realm orchestration still pending - needs 2 machines live.)
- **C3 ts-filter - NOT live-tested**: exercised only inside `connect`'s
  `_poll_reply`, which needs a real alive target (connect revives via evoke; a
  synthetic peer fails evoke -> connect can't run). Remains unit-verified (T17).
- **Side finding**: `/reload-plugins` restarted the MCP server (tools live) but
  left a STALE core_status (pid 7112, status=1, process long dead); the first
  kernel-bound MCP call's `ensure_core` detected the dead pid and started a fresh
  kernel from the on-disk new code. So MCP-server fixes go live on `/reload-
  plugins`; kernel fixes go live on the next `ensure_core` after the old kernel
  dies (or after `terminate.flag`).
- **Cleanup**: test conv dir removed; no stray python.exe (all calls foreground);
  `alive_conversations.json` back to `[]`; git clean.
- **Confidence**: high for C1/C2/C4/C5/R2 (live). C3 still unit-only. Remaining
  live gates: a real 2-CC multi-round conversation; cross-realm (WSL) re-run.

### T24 - Timestamp-ACK `listen` + kernel-atomic scan (cancel-safe messaging)
- **Bug (from the collab2 live test)**: a cancelled (ctrl+c'd) `listen` still
  archived messages in the MCP-server worker thread - the CC discarded the
  cancelled tool result, so the message was archived-and-lost (collab1's #7
  answer). Root cause: `listen` archived-on-read, and the scan wasn't
  synchronized to what the CC had confirmed. Diagnosed conclusively from both
  CC logs + the archived filenames (collab1's #7 was in log/, both convs still
  registered, step-22 listen returned only collab2's msg -> #7 was archived by
  the cancelled step-19 listen).
- **Fix (timestamp ACK + kernel-atomic scan)**:
  - `listen(sid, acked_ts, timeout)` -> `{messages, watermark}`. The CC keeps
    one watermark and passes it back on each call.
  - New kernel handler `listen_scan(sid, acked_ts)`: runs in the kernel's
    single thread (atomic w.r.t. send_message - no scan/write race). Archives
    `(to==sid, ts<=acked_ts)` [what the CC confirmed], returns `(to==sid,
    ts>acked_ts)` [peek, not archived] + `watermark = max returned ts`.
  - Cancel-safe by construction: a cancelled listen only PEEKED (archived
    nothing of the just-returned msgs); they re-deliver next call. Only what
    the CC confirmed (via the watermark it passes back) gets archived.
  - `close_connection(sid, toid, acked_ts)` uploads the watermark (persisted),
    sends a close notice (with an instruction telling the peer to
    `query_my_ACK_timestamp` + `close_connection` its side), unregisters. No
    pipe cleanup (ts-based ACK: un-acked msgs archived lazily via the watermark).
  - New `query_my_ACK_timestamp(sid)` recovers the stored watermark after
    compact/long-gap/restart.
  - Kernel persists `ack_timestamps.json` (in-memory update on every
    `listen_scan`; immediate write on `upload_ack_timestamp`; fallback save on
    exit).
  - `_MAX_SLEEP` cut 1.0s -> 0.2s (B5: lower listen poll latency).
  - Cross-realm: a WSL caller's `listen` also `call_remote("listen_scan")` to
    the host (where cross-machine convs live). `connect`/`_poll_reply` left
    as-is (B1: connect conveys no content; an interrupted connect = reconnect).
- **Method**: functional test (fresh T24 kernel, synthetic peers) - peek (msg
  stays in pipe after scan), ack (archived), partial-ack (msg2 archived, msg3
  remains), upload+query (persisted), close (notice + upload instruction
  delivered, pipe NOT cleaned, conv unregistered). All passed. py_compile +
  parity (v2_win==v2_wsl except .mcp.json).
- **Known residual risks (logged below as potential bugs, accepted)**: (A1)
  same-ms same-(from,to) send overwrites (`open(path,"w")`); (A2) wall-clock
  backward step can archive-without-return even with atomic scan. Both rare;
  not fixed (user decision: keep `<ts>__<from>__<to>.md` format, no seq#).
- **Confidence**: high for the mechanism (functional test covers cancel-safety,
  partial-ack, persistence, close). Live gate: reproduce the collab2 scenario
  (7 math rounds + mid-stream collab2 spawn, no loss) with real CCs.

### T25 - `evoke`/`spawn_cc_resume` cwd bug ("No conversation found" on reconnect)
- **Bug (from the reconnect live test)**: after closing a collaborator CC and
  asking the caller to reconnect, `connect`/`evoke` opened a cmd window showing
  `No conversation found with session ID: <sid>` - an error from the `claude`
  CLI, NOT from cc-communicate (that string is absent from the codebase). The
  session .jsonl existed and was valid at
  `~/.claude/projects/C--Users-Mocry/<sid>.jsonl` (177KB, 153 lines); the
  caller's `query_session` had even returned the correct `cwd: C:\Users\Mocry`.
  But `evoke` still failed both times (inside `connect`, and called directly).
- **Root cause**: `spawn.spawn_cc_resume` did NOT pass the target session's cwd
  - it ran `claude --resume <sid>` in the kernel's cwd (`data/server/`, set by
  `check_core._spawn_kernel` `cwd=SERVER_DATA_DIR`). Claude Code stores sessions
  per-project (`~/.claude/projects/<encoded-cwd>/<sid>.jsonl`) and `--resume
  <sid>` looks the session up WITHIN the current project (cwd-scoped). From
  `data/server/` it searched the wrong project dir, didn't find `<sid>` (which
  lives under `C--Users-Mocry`), and printed "No conversation found". The known
  cwd was thrown away: `user_functions.evoke` forwarded only `session_id`, and
  `kernel_api.evoke` never read `sessions[sid]["cwd"]`. (The docstring's "cwd is
  restored by --resume" assumption was wrong - `--resume` restores the
  conversation, NOT the process cwd, and the lookup happens before any restore.)
- **Evidence**: the error window's prompt was `…\data\server>` (the kernel cwd);
  the .jsonl was valid at `C--Users-Mocry`; `Popen(cwd=X)` + `cmd /c start` was
  verified to launch the child in cwd=X.
- **Fix**: `kernel_api.evoke` reads `cwd = sessions[sid]["cwd"]` and passes it
  to `spawn.spawn_cc_resume(sid, prompt, cwd)`. `spawn_cc_resume` accepts `cwd`
  and sets it via `Popen(cwd=…)` (Windows) / `_tmux_spawn(cwd or "", …)` (WSL,
  already wired with `-c cwd`). Same change applied to `spawn_cc_new` (latent:
  its `start /D <path>` broke on space-containing cwds like
  `C:\研究生\实习\learn AI\…` - switched to `Popen(cwd=…)`, robust to spaces).
  The (currently dead - no RPC caller) `kernel_api.spawn_cc_resume` kernel
  function + its dispatch also accept `cwd` for forward-compat. Both realms
  (parity identical); 16 MCP tools unchanged.
- **Method**: py_compile clean (6 files); parity diff identical (spawn.py /
  kernel_api.py / kernel.py); unit test - `evoke` passes `sessions[sid]["cwd"]`
  to `spawn_cc_resume` (cwd present -> passed; cwd missing -> None fallback;
  unknown sid -> "failed, session unknown"). `Popen(cwd=…)` child-cwd
  inheritance verified separately.
- **Note on the eventual successful resume**: a `start` event with
  `source:resume, cwd:C:\Users\Mocry` fired ~105s after the last end - but the
  .jsonl has NO entries after the original run's close, so that resumed session
  started idle (never processed the evoke prompt). It was not the evoke that
  succeeded (evoke ran from `data/server/` and failed both times); almost
  certainly a manual resume. The fix makes the automatic `evoke` path work.
- **Confidence**: high for the mechanism (unit + cwd-inheritance verified). Live
  gate: reconnect to a real closed CC via `evoke`/`connect` and confirm it
  resumes from the correct cwd (no "No conversation found" window).

### T26 - LIVE: 1v4 multi-collaborator validation (T24 cancel-safe confirmed)
- **Test (user-run, Windows host, T24+T25 kernel)**: 1 caller + 4 collaborators,
  4 separate conversations, 10+ rounds each, all ended cleanly.
- **Result**: SUCCESS - no message loss across 4 concurrent peer conversations
  over many rounds; clean `close_connection` ends on all sides. This is the live
  gate for T24's cancel-safe timestamp-ACK messaging in a real multi-peer
  scenario - the original collab2 cancel-loss bug is gone.
- **T25 (reconnect cwd fix)**: deployed in the same kernel; the 1v4 ran on T25
  code with no regression. A dedicated close-then-`evoke` reconnect remains the
  explicit T25 gate (the 1v4 did not necessarily exercise reconnect).
- **Confidence**: T24 cancel-safe messaging now LIVE-confirmed (high). Remaining
  live gates: a real reconnect of a closed CC (T25), and the cross-realm (WSL)
  re-run (WSL kernel needs a restart to pick up T24/T25).

### Potential bugs (accepted risks, not fixed)

- **PB-1 (A1) - same-ms message overwrite**: `send_message` writes
  `<ts>__<from>__<to>.md` with `open(path, "w")`. Two sends in the same
  millisecond with the same (from, to) collide on the filename -> the second
  overwrites the first -> first message lost before any scan. Low probability
  (the kernel processes sends sequentially; each takes ~1ms+; same-pair sends
  are usually seconds apart). Fix when adopted: `O_EXCL` create + `__N` suffix
  retry (no counter needed). **Resolved by HP-01**: filenames carry per-store
  sequence + message_id, so same-ms sends can never collide (covered by
  tests/unit/test_message_record.py::test_burst_same_ms_no_overwrite).
- **PB-2 (A2) - clock-backward archive-without-return**: the watermark is a
  wall-clock ms (`time.time()*1000`). If the clock steps backward (NTP, manual),
  a message written *later* can get an *earlier* ts; `archive(<=watermark)`
  then eats it without it ever being returned. Atomic scan does NOT fix this
  (it's a timestamp, not a monotonic seq). Windows NTP usually slews (no
  backward step) - rare. Fix when adopted: a persisted monotonic counter
  (seq#) as the watermark unit. **Resolved by HP-01**: ordering is by per-store
  sequence, never created_at_ms (covered by
  tests/unit/test_message_record.py::test_clock_backward_still_sequence_ordered).
- **PB-3 - cross-realm clock skew**: a WSL caller's `listen` merges the local
  (wsl clock) and host (host clock) watermarks. If the clocks differ, a later
  wsl message can have a ts below the merged watermark and be archived-without-
  return on the wsl side. Same-machine (host, the current test scenario) is
  unaffected (one clock). Fix when adopted: per-machine watermarks or a global
  seq#. **Resolved by HP-02**: per-store cursors replace merged watermarks —
  no cross-store sequence comparison, no cross-clock judgement (covered by
  tests/unit/test_cursor_ack.py; live gate L3, T33).

---

## §2 To-be-tested (need user / WSL deployment)

> **Status (2026-07-31): all B1–B7 resolved** — each item carries its
> CONFIRMED/DONE update below. Section kept as historical reference; new
> live-gate results are recorded in §1 as T# entries.

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
- **Update (T11)**: CONFIRMED on Windows host - `claude --resume` fires
  SessionStart (hook logged `source=resume`); `my_session_id` returns the real
  sid. WSL scenarios (a/b/c) still need v2_wsl deployed.
- **Update (T16 / WSL report)**: CONFIRMED on WSL - SessionStart fires on both startup AND resume; SessionEnd fires; kernel lazy-starts and replays session_ctrl events. B1 fully confirmed on both realms.

### B2 — #6 Trust dialog skip
- **What**: does `--dangerously-skip-permissions` let a spawned CC start without
  the workspace-trust prompt blocking the prompt?
- **Method**: `spawn_cc_new`/`spawn_cc_resume` (or manual `claude
  --dangerously-skip-permissions <prompt>`); observe whether the CC reaches the
  REPL and processes the prompt.
- **Expected**: no trust dialog; CC enters REPL and runs the prompt.
- **Who**: me or user (spawns a real CC).
- **Update (T12)**: PARTIALLY CONFIRMED - a spawned CC (45e9bb6e, via
  create_collaborator) started with `--dangerously-skip-permissions`, reached
  the REPL, and processed the prompt (called my_session_id + listen) with no
  trust-dialog block. The flag works.
- **Update (T15 / WSL report)**: CONFIRMED on WSL too - spawned CC 3392e304 reached the REPL with --dangerously-skip-permissions, no trust dialog. B2 fully confirmed on both realms.

### B3 — BUG-1 end-to-end
- **What**: a spawned/evoked CC (whose prompt contains "cc-communicate") can call
  `my_session_id` and get its sid.
- **Method**: after B2 unblocks spawning, spawn a CC with the evoke prompt; have
  it call `my_session_id`.
- **Expected**: returns a sid (was "failed, could not find claude ancestor"
  pre-fix).
- **Who**: me (after B2).
- **Update (T12)**: CONFIRMED - the spawned CC 45e9bb6e called my_session_id and
  got its sid (B2 unblocked by the same spawn).
- **Update (T15 / WSL report)**: CONFIRMED on WSL - 3392e304 called my_session_id and got its sid.

### B4 — connect end-to-end (single machine)
- **What**: two CCs on the same machine: connect -> hello -> reply -> succeed.
- **Method**: two live CCs (both with plugin); `my_session_id` each; `connect`;
  the target listens + replies.
- **Expected**: "connect succeed; reply: ...".
- **Who**: user (drives two CCs) or me driving both.
- **Update (T13)**: CONFIRMED - connect end-to-end via create_collaborator:
  81e4c033 connected to spawned a1e02819, hello sent, reply received via
  send_message, "connect succeed". Single-machine connect fully works.
- **Update (T15 / WSL report)**: CONFIRMED on WSL - create_collaborator spawned 3392e304, "connect succeed; reply: Hello back from 3392e304...". Plus the full bidirectional lifecycle (send_message -> pipe -> listen -> reply -> caller listen -> close_connection -> clean shutdown). B4 fully confirmed on both realms.

### B5 — Cross-realm e2e (Phase 2) + remote wake
- **What**: WSL CC ↔ host CC connect/send/listen/close; + remote-wake.
- **Method**: deploy v2_wsl in WSL; run `machine_add.py` (host) +
  `machine_sign_up.py` (WSL); WSL CC connects to host CC (and reverse); then
  kill host kernel, WSL CC connects again -> host kernel should wake (Amd8).
- **Expected**: cross-realm connect succeeds; remote-wake restarts the host
  kernel (core_status goes 0 -> 1).
- **Who**: user + me.
- **Update (T16)**: machine_identity stale-type cache fixed (was blocking cross-realm - WSL was misidentified as win-host). Handshake now DONE (B7). B5 unblocked: need a WSL CC running (user spawns), then host CC connect(81e4c033, <wsl_sid>) cross-realm + remote-wake (kill host kernel, WSL CC reconnects -> host kernel wakes, Amd8).
- **Update (B5 connect CONFIRMED)**: host CC 81e4c033 -> WSL CC 6ee1ed2e cross-realm connect SUCCEEDED on retry. First attempt timed out (WSL-CC did not act on its listener notification during the 300s window - NOT a routing bug; the WSL listener HAD caught the hello). After WSL-CC re-armed + committed to prompt reply, connect(81e4c033, 6ee1ed2e, 300) -> "connect succeed; reply: WSL-CC reply: hello received, channel established. Cross-realm WSL<->Host connect confirmed." Hello ts 1784113771564 -> reply ts 1784113781969 = 10.4s round trip. Conv registered on host (cross-machine store=host); query_conversations(81e4c033) sees 6ee1ed2e; both msgs archived pipe->log. T14/T15/T16 all live-confirmed via this cross-realm path. Fallback bridge file D:/temporary_bridge.txt (append-only) used for sync. Remote-wake (Amd8) CONFIRMED: killed host kernel (pid 16492), WSL call_remote(host, check_alive, 81e4c033) returned 1 in 10.4s (= 10s dead-window + _wake_remote ran python.exe wake_kernel.py via WSL interop -> new host kernel pid 27752). Unicode wake_script_native path worked through WSL->Windows interop. Host MCP tools verified with new kernel (check_alive 81e4c033=1). B5 FULLY DONE (connect + remote-wake). Remaining: bidirectional ping (optional), B6 (9p visibility).

### B6 — 9p dir-change visibility
- **What**: latency for a host-written file to appear in WSL `os.listdir(/mnt/c/)`.
- **Method**: host writes a file; WSL polls listdir; measure time-to-visible.
- **Expected**: < the listen.py poll window (2s) + settle (3s); if larger,
  listen.py cross-realm detection may lag -> adjust.
- **Who**: me (host write + WSL poll).
- **Update (B6 CONFIRMED)**: host wrote 25 probe files to v2_win/.../data/_9p_test/; WSL poller measured t_seen (WSL clock) - file_mtime (NTFS UTC) for each. Results: min=0.003s max=0.025s avg=0.013s median=0.013s; 25/25 within 0.5s (all within 25ms). 9p dir-change visibility is essentially instant (<25ms, dominated by the 20ms poll interval). listen.py's 2s poll window + 3s settle is ample - no adjustment needed. (WSL/Windows clocks aligned to ~0.1s, so the one-way mtime-based measurement is valid.) B6 DONE.

### B7 — handshake round-trip
- **What**: `machine_add` (host) + `machine_sign_up` (WSL) complete and both
  `machine_info_log/` get entries with correct data_dir/wake fields.
- **Method**: run both scripts; inspect `data/machine_info_log/*.json` on both
  sides; verify `data_dir` is the peer's perspective and `wake_script_native`
  is peer-native.
- **Expected**: both sides registered; fields correct.
- **Who**: me (after v2_wsl deployed).
- **Update (B7 CONFIRMED + T16 live)**: ran machine_add.py (host) + machine_sign_up.py (WSL, ~/projects/v2_wsl). Mutual registration succeeded. Host's WSL entry: data_dir=//wsl.localhost/Ubuntu/... (host perspective), wake_interpreter=python3, wake_script_native=WSL-native, distro=Ubuntu. WSL's host entry: data_dir=/mnt/c/... (WSL perspective), wake_interpreter=python.exe, wake_script_native=host-native, distro=null. C:\ clean (no residue). query_machines (host MCP) sees WSL peer 3b870f0d. T16 live-confirmed: WSL machine_identity regenerated as wsl-ubuntu (id 3b870f0d), not win-host. B7 DONE.

---

## §3 Confidence summary

| Area | Confidence | Reason |
|---|---|---|
| Code correctness (logic, imports, syntax) | high | T1/T2/T6 + code review |
| BUG-1, BUG-5 fixes | high | T2/T3/T6 |
| Local kernel + RPC lifecycle | high | T4/T7 |
| listen.py local path | high | T5 |
| connect end-to-end | high | T13/T15 (Win) + WSL report: connect succeed end-to-end on both realms; B4 confirmed |
| cross-realm (call_remote, wake, handshake) | HIGH | B7 handshake DONE + B5 cross-realm connect CONFIRMED (host->WSL, 10.4s) + remote-wake (Amd8) CONFIRMED (WSL woke dead host kernel, new pid) + B6 9p visibility CONFIRMED (~13ms); T14/T15/T16 live-confirmed; only optional bidirectional ping remains |
| 9p cross-realm file visibility (B6) | high | measured ~13ms avg (max 25ms), 25/25 < 0.5s; listen.py 2s window ample |
| JS hook (registrar.js/proc.js) | high | T10 - liveProcs + isClaudeCmd quote bugs fixed; live chain verified |
| `--resume` SessionStart (#1) | high (Win + WSL) | T11 (Win) + WSL report: --resume fires SessionStart on both realms; B1 confirmed |
| cross-session discovery + liveness | high | T11 - query_session + check_alive across two live CCs |
| trust flag (#6) | high | T12 (Win) + WSL report: spawned CCs reach REPL with --dangerously-skip-permissions on both realms |
| connect reply matching (C3) | high (unit) | T17 - ts-filter rejects stale close-notice + self-connect hello; unit-tested. Live needs a real connect target (synthetic peer fails evoke) - T23 |
| blocking listen + keep-listen law (C2) | high (LIVE) | T18/T23 - blocking MCP `listen` confirmed live: 8s block returned cleanly + delivered real messages; CC tolerates the blocking call (gate PASSED) |
| listen.py UTF-8 / decode hardening (C5) | high (LIVE) | T19/T23 - unicode (`你好...こんにちは`) delivered via CLI + MCP listen, exit 0, NO exit-1; confirmed on Chinese Windows |
| non-blocking terminate (C1) | high (LIVE) | T20/T23 - `close_connection` returns `{closed:true}` immediately, delivers close notice, unregisters; confirmed live |
| conv registration persistence (R2) | high (LIVE) | T21/T23 - `alive_conversations.json` written + loaded across kernel restart; send succeeded post-restart w/o re-register; kernel.log `loaded 1 convs` |
| handshake guide + tool (C4) | medium (LIVE tool) | T22/T23 - `help_connect_machines` returns guide (live); cross-realm exec proven by Amd8; full orchestration pending |
| cancel-safe listen (T24) | high (LIVE) | T24/T26 - timestamp-ACK `listen` + kernel-atomic `listen_scan`: peek (no archive-on-read), archive only what CC confirmed. **LIVE-confirmed (T26): 1v4, 4 peers, 10+ rounds each, no loss, clean end** |
| ACK watermark persistence (T24) | high (func) | T24 - `ack_timestamps.json` (in-mem on listen, persist on close/exit); `query_my_ACK_timestamp` recovers it. Functional test passed |
| evoke cwd / `--resume` lookup (T25) | high (unit) | T25 - `evoke` passes `sessions[sid]["cwd"]` to `spawn_cc_resume` (Popen cwd, not `start /D`); `claude --resume` is cwd-scoped (per-project .jsonl). Unit-tested + cwd-inheritance verified. Live gate: reconnect a real closed CC |

> **Note (post-v0.2.0 robustness pass):** C1/C2/C4/C5/R2 are **implemented +
> unit-tested + parity-verified + LIVE-verified** (T23, real MCP tools + fresh
> kernel). C3 remains unit-only (live needs a real `connect` target). The two
> earlier real-scene failures (background listen exit-1; collaborator stopped
> listening + bash loop) are addressed by C2/C5 and confirmed live. **T24
> (timestamp-ACK + kernel-atomic scan) fixes the collab2 cancel-loss bug**
> (cancelled listen archiving-and-dropping messages): functional-tested AND
> **LIVE-confirmed (T26: 1v4, 4 peers, 10+ rounds, no loss, clean end)**. **T25
> fixes the reconnect "No conversation found" bug** (`evoke`/`spawn_cc_resume`
> now pass the session's cwd so `claude --resume` runs in the right project):
> unit-tested + cwd-inheritance verified, ran in the 1v4 kernel with no
> regression; a dedicated reconnect test remains. Remaining live gates: a **real
> reconnect of a closed CC** via `evoke`/`connect` (T25), and a **cross-realm
> (WSL) re-run** (WSL kernel needs a restart to pick up T24/T25).
> Accepted residual risks: PB-1 (same-ms overwrite), PB-2 (clock-backward),
> PB-3 (cross-realm clock skew) - see "Potential bugs" above.

### T27 — HP-03 journal per-mutation fsync blocks event loop → spawn race (LIVE)

- **Bug (from Wave 1 live testing)**: `create_collaborator` spawned TWO CCs
  (one connected, one couldn't get session_id) or spawned one + an error window
  with a `data/` path. The second process was `claude --resume <sid>` launched
  by `connect → evoke` — a pre-existing race (new CC's SessionStart not yet
  processed → `check_alive` fails → `evoke` triggers a second `spawn_cc_resume`).
- **Why Wave 1 made it worse**: HP-03's `operation_journal.save()` (with fsync)
  was called on EVERY mutation inside `drain_queue` — `register_conversation`,
  `send_message`, etc. each triggered a synchronous disk sync. These competed
  with `process_session_ctrl_event` in the kernel's singleton event loop,
  delaying SessionStart processing and widening the race window.
- **Diagnosis**: traced the full `create_collaborator → connect → check_alive →
  evoke → spawn_cc_resume(cwd=None) → kernel cwd (data/server/)` chain. The
  error window's "data path" confirmed `spawn_cc_resume` ran from the kernel's
  cwd because the SessionStart event hadn't been processed → session had no
  `cwd` record → `evoke` passed `cwd=None` → child inherited kernel's
  `SERVER_DATA_DIR`.
- **Fix**: journal save is now batched — once per `drain_queue` cycle, not per
  mutation (`journal_dirty` flag). The journal is the "fast dedup path"; domain
  keys (message_id) are the crash-surviving truth per the HP-03 design spec.
  Losing unflushed journal entries on crash is documented-safe: retry is caught
  by domain dedup (send via message_id, register/unregister naturally idempotent).
- **Files**: `kernel.py::drain_queue` (v2_win + v2_wsl, parity verified).
- **Method**: analyzed the double-spawn + error-window symptoms; identified
  per-mutation fsync as the event-loop blocker; implemented batch save; 50/50
  tests green + parity OK. Pending live re-test with real `create_collaborator`.
- **Confidence**: high for the mechanism (the fsync bottleneck is eliminated;
  domain dedup covers the relaxed durability). Live gate: clean
  `create_collaborator` with exactly one spawned window, no error window.

### T29 — Task-1 brief defect: unit test ran the real full suite → infinite subprocess recursion

- **Bug**: `tests/unit/test_run_regression.py::test_tree_without_server_py_is_red`
  (transcribed from the Task-1 brief) did not monkeypatch `pytest_run`/`parity_run`.
  `main()` evaluates all three tiers unconditionally, so `pytest_run()` spawned
  `sys.executable -m pytest -q` (the FULL suite, per pytest.ini testpaths) — which
  re-collects `test_run_regression.py` itself, so `test_tree_without_server_py_is_red`
  ran again inside the child and spawned yet another full suite. Unbounded
  recursion: 46 nested `python -m pytest -q` processes observed before the chain
  was killed. Contradicts the file's own docstring ("No subprocess here") and the
  brief's expected outcome (6 PASS).
- **Fix**: added two monkeypatch lines to that test —
  `pytest_run` and `parity_run` stubbed to `(PASS, "ok")`; the REAL `syntax_check`
  is kept (the vacuous-pass guard is a T0 concern, and with tmp trees it spawns
  no subprocesses). The brief's code block was amended + erratum noted.
- **Files**: `tests/unit/test_run_regression.py` (Task 1), `task-1-brief.md`.
- **Method**: unit run hung >120s; process tree showed the nesting chain; killed
  all 46; verified `git status` clean (trees untouched, parity intact); applied
  2-line fix; `6 passed in 0.03s` (zero subprocesses); full suite 56 passed +
  PARITY OK unchanged.
- **Confidence**: high — recursion mechanism proven by the process tree; green
  after fix; full suite + parity re-verified (56 passed, PARITY OK (29 files)).

### T30 — L1 live gate RED: SessionStart double-fire stale pid → check_alive false-dead → double spawn (FIXED)

- **Bug**: create_collaborator spawned TWO windows. The child's SessionStart hook
  fired twice ~20ms apart; the two firings resolved DIFFERENT claude.exe pids
  (17140 real / 33060 transient, already dead). Kernel _handle_start is
  last-write-wins -> dead pid became primary -> check_alive returned 0 for a
  LIVE session -> connect's revive path evoked a second `claude --resume` window.
  (T27's batch-save fix was not the full story: this is a registration-quality
  race, not an event-loop stall.)
- **Fix**: check_alive falls back across `known_pids` (every (pid, start_time)
  ever recorded for the sid, maintained by _handle_start, bounded to 8); a
  match promotes to primary; dead candidates pruned. Covers both realms.
- **Method**: live repro (window count + pid evidence + session_ctrl events) →
  TDD (5 new tests in tests/unit/test_check_alive_fallback.py) → full suite +
  parity → L1 re-run.
- **Re-run (L1 PASS)**: fresh kernel (fixed code, pid 34276), one
  create_collaborator → **exactly ONE spawned window** (20148), no error
  window, no resume spawn. The hook double-fired AGAIN (two start events 23ms
  apart: pid 20148 real / pid 34948 transient — 34948 confirmed dead), and
  check_alive fell back to 20148 → connect succeeded, check_alive(sid)=1.
  The fallback works exactly as designed under the real failure pattern.
- **Confidence**: high — mechanism fully evidenced from live artifacts; unit
  tests lock the fallback/promote/prune semantics; re-run passed live.

### T31 — L3 live gate finding: cross-realm spawn blocked by host-side cwd validation (FIXED)

- **Bug**: create_collaborator(machine=<wsl peer>, cwd=/home/...) from the host
  returned INVALID_ARGUMENT - mcp_server entry validated the peer-perspective
  cwd with host semantics (os.path.isabs on Windows rejects /home/...), so
  every host->WSL spawn was blocked before the RPC left the host.
- **Fix**: validation.validate_spawn_entry - caller_sid always validated; cwd
  validated only for LOCAL spawns (machine is None); the peer kernel
  re-validates peer cwds with its own filesystem semantics at dispatch.
- **Method**: live repro (L3 gate: cross-machine create_collaborator) → TDD
  (3 tests in tests/unit/test_validation.py) → full suite + parity → L3 re-run.
- **Confidence**: high — live repro exact; entry rule now matches the
  validation authority split (local entry, peer entry re-validates).

### T32 — L3 live gate finding: headless WSL kernel has no claude_bin → spawn falls back to interop Windows claude (FIXED)

- **Bug**: host->WSL create_collaborator spawned the WSL CC but it never
  registered (failed, new session did not register within 30s). The WSL kernel
  was started headless (host wake), so machine_identity._detect_claude_bin's
  ancestor walk found no claude -> claude_bin null -> spawn._claude_bin fell
  back to bare `claude`, which on WSL resolves to the interop WINDOWS claude.exe
  (C13; /mnt/c/Users/Mocry/AppData/Roaming/npm is on the WSL PATH) - whose
  SessionStart hooks are Windows-side, so no start event landed on the WSL
  kernel. A native WSL claude exists at ~/.npm-global/bin/claude. Additionally,
  load_or_create only upgraded identity when the claude_bin KEY was missing -
  a persisted null stayed null forever.
- **Fix**: machine_identity._native_linux_claude() filesystem search (static
  candidates ~/.npm-global/bin, ~/.local/bin, /usr/local/bin + npm-prefix
  derived, rejecting /mnt/ interop paths) as the fallback in _detect_claude_bin;
  load_or_create re-detects a null claude_bin on Linux. Covers both realms.
- **Method**: live repro (L3 gate: host->WSL spawn, tmux cmdline shows the
  interop claude.exe) → TDD (4 tests in
  tests/unit/test_machine_identity_claude_bin.py) → full suite + parity →
  L3 re-run.
- **Confidence**: high — mechanism evidenced from the live tmux cmdline +
  WSL PATH + identity file; unit tests lock search/reject/upgrade semantics.

### T33 — Wave 1 live gates L2 (reconnect) + L3 (cross-realm cursors) PASS

- **L2 (reconnect, T25)**: real CC-A (daf6acf9) closed (check_alive 0) →
  evoke → `claude --resume` resumed with **cwd == P** (resume start event
  cwd field verified) → check_alive 1 → send_message delivered (child
  confirmed by message_id) → child's reply received via listen_v2 (per-store
  cursor). No "No conversation found". Full round trip on the new kernel.
- **L3 (cross-realm cursors, R2)**: fresh handshake (WSL id 4cefe529,
  stale 3b870f0d retired), host→WSL spawn via the T31/T32-fixed path →
  WSL CC 5372d1f1 spawned with the NATIVE WSL claude
  (~/.npm-global/bin/claude, not interop) + connected. A sent 3 → B
  listen_v2 saw seq 21/22/23 (host store, monotonic) → B partial-ACKed
  cursor=22 → 21/22 archived, 23 re-delivered → B sent 2 back (plus its own
  autonomous ack) → A listen_v2 saw seq 24/25/26 → A re-listened with the
  same cursor → **empty** (no re-delivery). Per-store cursor independence +
  zero loss + no cross-clock interference, live.
- **Findings fixed en route**: T31 (peer cwd validation deferral), T32
  (headless WSL kernel claude_bin → native Linux claude search).
- **Confidence**: high — both gates driven live with real CCs on both
  realms; cursor archive semantics verified at each step.

### T34 — Wave 1 live gate L4 (multi-collab stress) PASS

- **Scenario**: coordinator + 4 collaborators, 5 rounds, 1 tagged message per
  collaborator per round (20 total, tags L4-r1-c1..L4-r5-c4), replies collected
  via listen_v2 with per-store cursors. Spawned on the batch-journal-save
  kernel (T27 fix + T30 fix).
- **Result**: 20/20 messages delivered; **20/20 tag confirmations received**
  (one per sent message); store-level verification: 20 sent records with 20
  unique message_ids, 20 tag replies with 20 unique tags — **zero loss, zero
  duplicates**. Re-listen with the final cursor returned empty (cursor
  archiving correct under load). Clean close_connection on all 4.
- **One-off observation (CORRECTED, see T35)**: the 4th spawn appeared
  unresponsive to hellos; initially recorded as a plugin-boot flake. The
  child was actually functional — its `my_session_id` was failing due to the
  `session_by_pid` stale-pid hole (T35), which sent it into diagnosis mode
  instead of listening. The child diagnosed and fixed the bug itself (see
  T35); the wedge was a real cc-communicate bug, not a child-boot flake.
- **Confidence**: high — every sent message individually confirmed by tag;
  store inspected directly for dupes/loss.

### T28 — Wave 1 exit regression gate (scripted tiers + live L1–L4) — GATE PASS

- **Method**: `py -3 tools/run_regression.py --tier auto` → T0 syntax / T1
  pytest / T2 parity; then live checklists L1 (spawn-race re-test) / L2
  (reconnect) / L3 (cross-realm cursors) / L4 (multi-collab stress) driven per
  the script's printed checklists; each live gate RED → bug T# + fix + re-run.
- **Result**: T0 `PASS (40 .py + 2 .js parsed clean)` / T1 `PASS (72 passed, final re-run)`
  / T2 `PASS (PARITY OK 29 files)` → GATE PASS (scripted). Live gates:
  L1 `PASS` (one window; T30 fix verified live under the real double-fire) /
  L2 `PASS` (evoke→resume in cwd P, full round trip) / L3 `PASS` (per-store
  cursors, partial ACK, no re-delivery; T31+T32 fixes) / L4 `PASS` (20/20
  sent, 20/20 confirmed, zero loss/dupes). Per-gate evidence in T30/T33/T34.
- **Confidence**: high — scripted tiers machine-checked; live gates driven
  with real CCs per checklist; four real bugs found + fixed en route
  (T30/T31/T32/T35), each with unit tests + parity.

### T35 — L4 live gate finding: session_by_pid stale-pid hole breaks my_session_id (FIXED)

- **Bug**: the 4th L4 collaborator's `my_session_id` failed. Its SessionStart
  double-fired 7ms apart (T30 pattern): first pid 34392 (real), then pid 43212
  (transient, dead). T30's known_pids fallback fixed `check_alive` but NOT
  `session_by_pid` — it scans only `sessions[sid]["pid"]` (last write = the
  dead transient), so the real pid never resolved to the sid and the
  collaborator could not identify itself (its first protocol step). The
  collaborator itself diagnosed this, wrote the fix, and verified it against
  the real event files (its transcript is the diagnosis record).
- **Fix**: `session_by_pid(sessions, alive_sessions, pid)` — primary scan
  unchanged; on miss, fall back across every sid's `known_pids` with the same
  liveness rule as `check_alive` (new `_pid_live` helper). Covers both realms.
- **Method**: live repro (L4 gate: spawned collaborator's my_session_id
  failure; real event files show pids 34392/43212) → TDD (4 tests appended to
  tests/unit/test_check_alive_fallback.py) → full suite + parity →
  commit.
- **Confidence**: high — verified against the real double-fire events
  (session_by_pid(34392) -> sid); unit tests lock fallback/primary/unknown
  semantics.

### T36 — Wave 2 D8 live probe: env→hook→SessionStart chain carries spawn_token (PLAN A CONFIRMED)

- **Probe**: spawned a real CC via `cmd /c "set CC_COMMUNICATE_SPAWN_TOKEN=probe-tok-1 && start claude"`; the resulting `start_*.json` event carries `"spawn_token": "probe-tok-1 "` (sid 5041613c, source=startup). The env var survives cmd → claude → SessionStart hook → registrar.js. Plan A (env injection, D8) is live; plan B (pending_spawn claim) still lands as the idempotent fallback.
- **Discovery (environment mechanic)**: real CC sessions on this machine resolve the plugin root to the REPO's `v2_win/cc-communicate` (their MCP children run `python -u .../v2_win/cc-communicate/server/mcp_server.py`) — NOT the installed copy at `~/.claude/plugins/cache/cc-communicate-local/cc-communicate/0.3.0` (ba35e31 snapshot, stale). Live gates therefore exercise the repo code directly; the installed copy needs no sync for wave testing.
- **Artifact note**: `cmd set VAR=x && cmd2` includes the trailing space in the var (`probe-tok-1 `); real spawns inject via Python env dicts (`{**os.environ, ...}`), which have no such artifact. A trailing space would fail `validate_spawn_token` charset — harmless here since plan A only reads the env value as an association key, but noted for plan B's claim path (tokens come from Python-side uuid4 hex).
- **Method**: live spawn on the user's desktop; event file inspected directly.
- **Confidence**: high — the exact chain D8 worried about, verified end-to-end with the real hook binary.

### T37 — Wave 2 live gates L5/L6: same-cwd concurrent spawns + correlated connect (PASS)

- **L5 PASS (HP-04 deliverable)**: two concurrent `spawn_collaborator` calls in the SAME cwd (`C:\Users\Mocry`) resolved to TWO distinct sessions (a6f8924e / cb5dfec6), each WorkerHandle bound to its own spawn_token (l5-token-a/b), both `connection_status=registered`, 4.6s elapsed. The old `find_new_session(cwd, since_ts)` same-cwd race is gone by construction.
- **L6 PASS (HP-05 deliverable, one caveat)**: connect with explicit `connection_id=l6-conn-...1` → worker (new prompt) came online, listened, replied; connect succeeded. **Hello record carries `kind: hello` + `correlation_id` (= the connection_id)** — verified in the real store (seq 85). D9 verified live: second connect with a different id → `CONFLICT {current_connection_id, status}`; same-id retry → `ok(reused=True)`. Correlation-matcher verified end-to-end through the REAL kernel: `send_message(..., correlation_id=...)` → record → `_claim_reply` picks it by correlation_id.
- **Caveat (environment, not code)**: the autonomous worker's reply OMITTED `correlation_id` (model prompt-following variance — the prompt text is correct and the worker is a fresh deepseek session), so the live connect succeeded via the **legacy single-candidate fallback** — which is itself live-verified (bonus). The correlation-matched reply path is pinned by the unit test `test_connection_id.py::test_connect_hello_carries_kind_and_correlation` (threaded, real files).
- **Environment finding (recorded)**: spawned CCs inherit the parent terminal's `ANTHROPIC_MODEL=glm-5.2` (stale session env var from earlier glm experiments; NOT in settings.json which correctly maps deepseek). The API gateway rejects glm-5.2 → spawned workers sat idle at their first model call. Fixed by `env -u ANTHROPIC_MODEL` for gate runs; user should `set ANTHROPIC_MODEL=` in their main terminal. This also explains why L5 workers never acted interactively.
- **Method**: script-import coordinator (new user_functions code, real data root) + real spawned CCs; evidence from `data/conversations/*/log/*.json` records + WorkerHandles.
- **Confidence**: high — real store records, real kernel, real CCs; the one model-side omission is documented and orthogonal to the code.

### T38 — Wave 2 live gate L2 + environment root-cause: kernel env inheritance breaks spawn transcript/resume (PASS after fix)

- **L2 PASS**: real CC closed → `evoke` (structured `{evoked: True, session_id}`) → revived with the session's cwd (T25 path through the new code) → `connect` succeeded with the resumed worker's reply. The wave's evoke/legacy-wrapper changes verified live.
- **Root cause found (environment)**: the kernel daemon, spawned from this CC session's shell, inherits `CLAUDE_CODE_CHILD_SESSION=1` (and `ANTHROPIC_MODEL=glm-5.2`, T37). Every CC it spawns carries the marker → **transcript saving is off** (statusline: "Transcript saving is off — inherited CLAUDE_CODE_CHILD_SESSION marker") → no `~/.claude/projects/*.jsonl` ever written → `claude --resume <sid>` fails with "No conversation found" from ANY cwd, which blocks both manual resume and the plugin's evoke path for those workers. The coordinator process env is IRRELEVANT: the spawn happens in the KERNEL process, which builds the child env from its own os.environ (fixed at kernel start).
- **Fix (gate-side)**: restart the kernel with a clean env (`env -u CLAUDE_CODE_CHILD_SESSION -u ANTHROPIC_MODEL`) → spawned workers are normal resumable sessions (transcript lands in the project dir; resume works). For production spawns from real user terminals this never applies (their claude isn't a child session); it only bites when CCs spawn CCs.
- **Method**: live spawn/close/evoke/connect against the real kernel; transcript + registry evidence; worker statusline showed the marker explicitly.
- **Confidence**: high — resume verified end-to-end after the clean-kernel restart.

### T39 — Wave 2 live gate scope decision: L3 (cross-realm cursors) + L4 (multi-collab stress) not re-run

- **Decision (user-approved)**: L3/L4 from the standing gate were NOT re-run this wave. Rationale: the wave's live risk areas were exactly the new spawn disambiguation (L5 PASS) and connection correlation (L6 PASS); L4 would re-verify the message path (send/listen unchanged except envelope wraps, unit-tested); L3 would re-verify cross-realm cursor semantics (unchanged) plus the new 3-line routing wrappers (activate/get/deactivate_connection), which mirror the already-live-proven `_register`/`_send` pattern and are unit-tested. Recorded rather than silently skipped.
- **Wave gate summary**: L1 PASS / L2 PASS (T38) / L5 PASS / L6 PASS (T37) / L3+L4 not re-run (T39). Auto tiers: T0/T1/T2 GATE PASS (117 tests, PARITY OK 29 files).
- **Confidence**: high for the run tiers; the skipped tiers carry the documented rationale above.

### T40 — HP-08 impl finding: module name `gc.py` collides with the Python stdlib `gc` (FIXED: renamed `cleanup.py`)

- **Method**: Task 3 of the HP-08 plan created `server/gc.py`; the first test run failed with
  `AttributeError: module 'gc' has no attribute 'pending_marker_expired'` — `importlib.import_module("gc")`
  (and any `import gc` in kernel.py/kernel_api.py) resolves to the already-imported STDLIB `gc`
  (garbage collector) in `sys.modules`, not the new file. Renamed the module to `server/cleanup.py`
  (function names unchanged: `run_gc`/`maybe_run_gc`/`pending_marker_expired`/`collect_candidates`);
  updated conftest reload list, test references, plan/spec docs. Also removed a stale `gc.py` that a
  pre-rename sync had copied into v2_wsl (parity was the detector).
- **Result**: FIXED — 7/7 gc tests pass, full suite green (133 tests), parity OK.
- **Confidence**: high. Lesson: avoid module names that shadow stdlib names in this sys.path-inserted
  test setup; parity gate caught the stale-tree residue.

### T41 — HP-08 unit acceptance: registered-but-idle kernel exits; restart reloads state; GC whitelist holds

- **Method**: unit (test_kernel_exit.py / test_gc.py / test_spawn_token.py / test_spawn_env.py / test_proc_pid_matches.py): exit predicate decoupled from registration (D10); R4 second queue scan (`_exit_decision`); GC whitelist (session_ctrl ≥7d, pending_spawn >TTL, responses ≥7d) never touches pipe/log (structural guardrail test); pending_spawn TTL un-poisons same-token retries; spawn env sanitization (T38 code-level fix); `proc.pid_matches` dedup; `run_gc` kernel-function dispatch. Full auto gate `py -3 tools/run_regression.py --tier auto` → GATE PASS.
- **Result**: PASS (144 unit tests; parity OK 30 files; auto gate GATE PASS). Live gates (full L1-L6, incl. the mandated L3/L4) deferred to the Wave 3 exit gate per the user's locked decision (run_regression.py now prints L5/L6 checklists too).
- **Confidence**: high for unit semantics; live verification at Wave 3 exit.

### T42 — HP-09 unit acceptance: RESOURCE_EXHAUSTED activated + artifact_refs + backpressure

- **Method**: unit (test_resource_limits.py / test_artifact_refs.py):
  over-limit inline text -> RESOURCE_EXHAUSTED envelope with
  {limit_bytes, actual_bytes} (dormant code activated); artifact_refs schema
  validation matrix (both trust boundaries), record payload carries refs,
  delivered via listen_v2 (raw record) + legacy listen/collect_messages;
  over-limit text WITH refs still rejected; per-pair unacked cap
  (CC_COMMUNICATE_MAX_BACKLOG) -> RESOURCE_EXHAUSTED retryable, releases
  after drain; backlog_stats kernel function per-partner counts+bytes.
  Also fixed a flaky test found by repeated suite runs (legacy-refs test:
  both messages shared sequence 1, so listdir order decided which message
  the second listen returned - test now acks the first message + shares one
  seq state). Full auto gate `py -3 tools/run_regression.py --tier auto`
  -> GATE PASS.
- **Result**: PASS (164 unit tests; parity OK; auto gate GATE PASS). Live
  gates (full L1-L6, incl. the mandated L3/L4) deferred to the Wave 3 exit
  gate per the user's locked decision.
- **Confidence**: high for unit semantics; live verification at Wave 3 exit.

### T43 — HP-10 unit acceptance: permission default flip (D4) + legacy marking + threat-model README

- **Method**: unit (test_permission_mode.py): validate_permission_mode
  matrix; spawn argv splicing (_permission_argv; spawn_cc_new default
  standard has NO --dangerously-skip-permissions, bypass has it;
  spawn_cc_resume default bypass); kernel dispatch routes permission_mode
  (spawn_cc_new standard / resume+evoke bypass); mcp_server
  spawn_collaborator default standard + pass-through + entry validation;
  WorkerHandle carries permission_mode; evoke override param;
  create_collaborator legacy suffix + explicit bypass; kernel log line for
  bypass spawns (caplog). README.md (plugin root) ships the D4 threat model.
  Full auto gate `py -3 tools/run_regression.py --tier auto` -> GATE PASS.
- **Result**: PASS (unit + auto gate; parity OK). Live gates (full L1-L6,
  incl. the mandated L3/L4) deferred to the Wave 3 exit gate per the user's
  locked decision.
- **Confidence**: high for unit semantics; live verification at Wave 3 exit.

### T44 — HP-11 unit acceptance: schema validation + migration tool (D2, custom data root recoverable)

- **Method**: unit (test_schema.py / test_kernel_schema_guard.py /
  test_migrate_data.py): schema_too_new matrix (int>supported only);
  unwrap dual-read (wrapped v1 + legacy flat/list); stamp_v1/wrap_v1
  migrate + idempotent no-op; validate_layout (dirs advisory, newer file
  REFUSED); kernel loaders skip newer-format state files with a loud log
  (file untouched); dual-read of sessions/alive_convs/ack_timestamps;
  writers emit {schema_version: 1, <key>: <payload>} (incl.
  upload_ack_timestamp); migrate_data.py CLI via subprocess: dry-run writes
  nothing, migration wraps/stamps the 4 registries, second run no-op,
  newer schema -> exit 1 + untouched. Full auto gate
  `py -3 tools/run_regression.py --tier auto` -> GATE PASS.
- **Result**: PASS (unit + auto gate; parity OK). Live gates (full L1-L6,
  incl. the mandated L3/L4) run NEXT at the Wave 3 exit gate per the user's
  locked decision.
- **Confidence**: high for unit semantics + CLI behavior; live gate pending.

### T45 — Wave 3 exit live gates L1/L2/L4/L5/L6 (full re-run per user decision; L3 blocked-environment)

- **Method**: script-import coordinator (`livegate-coordinator`, real data root) + real spawned CC windows on the
  user's desktop. Fresh kernel restarted with clean env (new Wave-3 code confirmed live: kernel log shows the HP-08
  start-GC deleting 128 stale session_ctrl events + 3 orphaned responses, violations=[] — live GC evidence).
  - **L1 spawn-race PASS**: spawn → exactly one registered worker per token (`bb105bac`, `f99fe4f6`, `9f5e3f76`,
    `0e204031`); same-token retry returns the SAME handle without re-spawn; check_alive=1; token map binds; the new
    WorkerHandle carries `permission_mode` (HP-10 live evidence).
  - **L2 reconnect DEGRADED (T46)**: dead (check_alive=0) → evoke → alive in 2s, resume lands in the original cwd
    (T25), and the saved transcript proves the T38 fix live (workers are resumable). Delivery AFTER resume FAILED
    twice (2/2): the resumed CC's cc-communicate MCP client comes up DISCONNECTED (see T46) → the worker cannot
    listen/ack → the delivery message stays in the pipe. Delivery path itself re-verified via fresh spawns (L4:
    10/10 acked). AR-04: this is `DEGRADED`, not "PASS + finding" - see T46.
  - **L4 multi-collab stress PASS**: 2 workers × 5 tagged messages = 10 sent, 10/10 acked (archived exactly once,
    zero loss/dup); one worker replied 5/5 with message_id echoes (collected via coordinator listen_v2, 5/5 matched);
    the other acked without replying (autonomy variance, not loss).
  - **L5 same-cwd spawns PASS**: 2 workers in the SAME cwd with distinct tokens → distinct sids, no session bleed,
    both alive.
  - **L6 correlated connect PASS**: connect with connection_id → correlation-matched reply from the worker; a second
    id → CONFLICT with current_connection_id in data; same-id retry → reused=True; close → info.json status=closed
    + closed_at_ms.
  - **L3 cross-realm NOT RUN**: needs the WSL side (v2_wsl deployment + WSL CC + handshake) — environment not set up
    this session. Recorded as blocked-environment, to run in a follow-up (it is the one gate the kimi-k3 mandate
    explicitly required).
- **Result**: L1/L4/L5/L6 PASS; **L2 DEGRADED (T46)**; L3 blocked-environment. Auto gate GATE PASS (193 tests,
  parity OK 32 files) was re-run before the live session.
- **Confidence**: high for the run gates; L3 explicitly pending.

### T46 — L2 DEGRADED: resumed CC's cc-communicate MCP client disconnected (CC v2.1.220 resume quirk)

- **Classification (AR-04)**: L2 = `DEGRADED`, NOT "PASS + finding" — those two
  statements cannot both be true in a capability contract. Split of the two
  halves: **process/session recovery SUCCEEDS** (resume lands in the original
  cwd, check_alive → 1), **communication recovery FAILS** on this CC version
  (the revived CC's MCP client comes up disconnected → delivery after resume
  2/2 failed). `evoke` promises process/session restore only; channel readiness
  is not restored. Contract fix: upper layer uses **spawn-fresh fallback** for
  H1 (fixed new workers; resume unavailable until a CC update re-tests green).
  Upgrade path: re-run L2 after a CC update; if the resumed round-trip passes,
  the status upgrades from DEGRADED.
- **Symptom**: after evoke→`claude --resume`, the revived worker's window reports "cc-communicate MCP server
  currently disconnected (tools unavailable)" — 2/2 resumes (f99fe4f6, 9f5e3f76). The delivery message sent to the
  revived worker is never acked (stays in pipe). Both resumed windows also showed a stray `❯ bypass` user line in
  the restored transcript that neither the user nor any cc-communicate code typed.
- **Evidence it is CC-side, not a cc-communicate regression**: the revived CC spawns its MCP server normally
  (python mcp_server.py, repo code, child of the revived CC, healthy 0% CPU); the worker's LAST tool call before the
  close succeeded (close_connection → {closed: true}); no error in cc-communicate code or kernel.log; original (non-
  resumed) workers listen/ack fine (L4 10/10); Wave-2 L2 passed the same flow, so the failure is intermittent.
  Hypothesis: during the transcript restore ("Cooked/Baked for 51-112s") the CC's MCP client times out the idle
  blocking-listen server and marks it disconnected; the `bypass` line is a CC permission auto-response artifact.
- **Action**: documented; re-test delivery-after-resume on a CC update or after investigating CC's resume↔MCP
  handshake. cc-communicate code untouched (no fix warranted without a reproducible cc-communicate-side cause).
- **Confidence**: high for the environment attribution; the exact CC-internal mechanism unverified.

### T47 — Wave 3 exit live gate L3 (cross-realm host↔WSL cursors, kimi-k3-mandated) PASS

- **Method**: real WSL2 (VS Code session, Ubuntu, peer id 4cefe529 registered since 2026-07-31). Synced the
  Wave-3 v2_wsl code to the WSL deployment (/home/mocry/projects/v2_wsl/cc-communicate; server/scripts/skills/
  .mcp.json, data/ untouched) via the //wsl.localhost 9P share; WSL kernel restarted (kernel.log: new-code start
  at 2026-08-03 01:56:57 WITH the HP-08 start-GC sweep line). Spawned the WSL worker via host-side
  call_remote(spawn_cc_new) -> tmux; registered 2s (2011c315-d0a3-4fcd-9c4a-47c8cb9476a2, token lgw1-tok).
  Flow: host A('livegate-coordinator') -> register pair (host store) -> sent 3 (seq 115-117); the real WSL CC
  acked all 3 through the routed host store in 12s (pipe drained, log archived - zero loss); B->A direction: 2
  replies routed to the host store; A listen_v2 cursor=0 -> 2 replies, re-listen same cursor -> 0 (no
  re-delivery); cursor maps: A's keys ONLY the host store (60ca2608), the WSL kernel's cursor state empty - no
  cross-store bleed.
- **Result**: PASS - cross-realm spawn/registration, delivery both directions, per-store cursor independence,
  zero loss/dup. Auto gate GATE PASS (193 tests, parity OK 32 files) unchanged.
- **Confidence**: high - real WSL CC, real cross-machine RPC (call_remote + routed store), per-kernel cursor
  state verified on both sides.

### T48 — Wave 3 external audit (kimi-k3) PASS

- **Method**: review package `docs/superpowers/reviews/2026-08-03-wave3-review-brief.md` sent to kimi-k3 after
  the full L1-L6 live re-run was pushed (b8b828a). Reviewer re-ran the verification themselves (full suite
  193/193, parity 32 files, GATE PASS, 72/72 Wave-3 new tests, dispatch routes, conftest reload list).
- **Result**: PASS — all four proposals (HP-08 kernel lifecycle + safe GC, HP-09 resource limits + artifact_refs +
  backpressure, HP-10 permission default flip, HP-11 wrap-migration) implemented correctly. No blocking issues,
  no fix-before-merge items. Highlights cited: structural GC whitelist, wrap-migration design correction (D2-a),
  additive-key artifact_refs, retryable backpressure semantics, honest T46 attribution.
- **Dispositions of the 4 minor notes (all accepted as recorded, none requires code)**:
  1. T46 re-test after a CC update — already standing (T46 action).
  2. known_pids bound-trim TypeError on None start_time — already recorded as a deferred minor (Wave-1 legacy);
     unchanged.
  3. cc-communicate-marketplace/ tree sync (Wave 1-3 fixes) — already standing release item; unchanged.
  4. Backpressure is a count cap, not a byte cap — already recorded as by-design deviation; unchanged.
- **Next**: Wave 4 (HP-13-A canonical single source + generated win/wsl artifacts) — the reviewer confirms it is
  the last wave and that the protocol is stable enough (193 tests + 6 live gates) to absorb its HIGH risk.
- **Confidence**: high for the audit outcome; dispositions are the reviewer's words mapped onto existing records.

### T49 — Wave 4 acceptance: HP-13-A canonical source + generated artifacts (auto + live smoke gates)

- **Auto gate**: T0 syntax PASS (44 .py + 2 .js), T1 pytest PASS (203), T2 parity PASS (32 files),
  **T2 artifacts PASS (33 files, templates pinned)** — GATE: PASS (`py -3 tools/run_regression.py`).
- **0-diff invariant**: `py -3 tools/build_artifacts.py generate` on the canonical v2_win tree produced
  `GENERATED v2_wsl/cc-communicate (33 files)` and an EMPTY `git diff --stat v2_wsl` — the committed artifact
  already matches generator output byte-for-byte.
- **LF pinning**: repo `.gitattributes` pins `v2_win/**`, `v2_wsl/**`, `tools/artifact_templates/**` to
  `text eol=lf` (CRLF hazard found in Task-2 review: core.autocrlf=true checkouts would write CRLF working
  copies → byte gates fail while `git diff` shows nothing; renormalize was a no-op — blobs already LF).
- **Live smoke gate (L7, driven from the session's real plugin on the canonical tree)**:
  spawn_collaborator(w4-smoke-tok) → worker 8678a175, cwd == repo, WorkerHandle `permission_mode: standard`
  (D4 live-confirmed) → correlated connect (reply matched w4-smoke-conn) → send 1 probe → worker ACKed the
  exact message_id (d11d0abce21c4be483ab0cd323cd390d, store seq 122→123) → check_alive worker == 1 →
  WSL peer 4cefe529 still registered, WSL session 2011c315 check_alive == 1 → cross-realm connect +
  probe → routed reply through the host store with the exact message_id (fb5e115b9b094d42b866a16a9fb91173,
  seq 127) → both connections closed clean.
- **Result**: PASS — install entry + live behavior unchanged through the canonical tree; cross-realm install
  path untouched. Wave 3's L1-L6 gates remain the standing protocol gates; this wave added L7 (smoke) to
  the checklists.
- **Confidence**: high — real CC worker, real WSL peer, real store records, auto gate re-run at exit.

### T50 — Acceptance-revision execution: AR-01~06 + N-01~03 (customer REVISE_REQUESTED -> re-acceptance ready)

- **Context**: customer acceptance review (`docs/superpowers/reviews/2026-08-03-hardening-acceptance-review.md`)
  returned `REVISE_REQUESTED` (Waves 1-4 architecture `ACCEPTED_IN_PRINCIPLE`; 6 AR proposals + 4 N-notes).
  K3 dispatched the task book (`docs/superpowers/plans/2026-08-03-acceptance-revision-plan.md`); executed
  inline per its 执行约束 (edit v2_win only -> `tools/build_artifacts.py generate` -> commit both trees).
- **AR-01 (P0, MCP pin)**: `server/requirements.txt` `mcp>=1.28` -> `mcp>=1.28,<2` (MCP 2.0 removed
  `mcp.server.fastmcp`; clean installs resolved to 2.0.0 and failed to import). Gate: 3 tests in
  `tests/unit/test_mcp_dependency_gate.py` (declaration pin, fresh-interpreter fastmcp import, dev-dep entry).
- **AR-02 (P0, transport honesty)**: `listen_v2`/`query_my_cursors` now track per-side scan success
  (`rpc_client.call` raises KernelError locally / `call_remote` returns None remotely). At deadline: local
  zero success -> `err(INTERNAL, retryable=True)` (no fake empty success); local ok + host fail -> result +
  `degraded_stores` marker; scanned messages never lost (local-dead + remote-messages returns them with the
  marker). 7 injection tests (3 mandated failure classes + full-success no-key + query_my_cursors pair).
- **AR-03 (P0, known_pids)**: `kernel.py` `_handle_start` bound-trim `sorted(known, key=known.get)` ->
  insertion-order `list(known.keys())[:-8]` (sorted() raised TypeError comparing None vs float on 9+ replay).
  start_time used only for PID-reuse validation. 4 tests: all-None, None+float mixed, PID dup + check_alive
  no regression, real restart replay via `process_session_ctrl_event` (10 events).
- **N-01/02/03**: `close_connection` reports failed best-effort steps via `degraded_steps` (clean path shape
  byte-unchanged; +1 test); `run_regression.pytest_run` prints stderr tail on RED (+1 test); new
  `requirements-dev.txt` (pytest) at repo root (asserted by the AR-01 gate file).
- **Auto gate (raw output)**: `py -3 tools/run_regression.py` ->
  T0 syntax PASS (44 .py + 2 .js) / T1 pytest PASS (**220 passed** = 204 + 16 new) /
  T2 parity PASS (32) / T2 artifacts PASS (33, templates pinned) / **GATE: PASS**.
  Artifacts regenerated after the SKILL.md change; 0-diff invariant holds.
- **AR-04 (P1, resume/L2)**: T46 + T45 reclassified `PASS + finding` -> **DEGRADED** (process/session
  recovery succeeds; communication recovery fails on CC v2.1.220, 2/2). Completion report §3.1 row +
  new §3.7 能力降级声明 (spawn-fresh fallback + CC-update re-test upgrade path); SKILL.md evoke entry
  DEGRADED note. No code change (T46 stays CC-side attribution).
- **AR-05 (P1, release surface)**: `cc-communicate-marketplace/README.md` top banner (option 2: 历史参考，
  不支持安装；权威实现 v2_win/ + v2_wsl/ build_artifacts.py 生成); tag **v0.4.0** on the delivery commit;
  acceptance review + completion report + task book + all fixes committed in the delivery commit.
- **AR-06 (P1, HP-12/G4 contract)**: completion report HP-12 -> `DEFERRED (分阶段接受)` everywhere
  (§1.1/§2/§3.1/§3.4/§4/§6.1/§8): H1 alternative observability (structured Result/Error incl.
  degraded markers + backlog_stats + run_gc(dry_run) + kernel log) + restart condition (进入 H2/H3 或
  第一次真实无法定位的传输故障); master plan §4.1 可观测 promise -> phased wording (+§4.4 note).
- **Re-acceptance gates (7/7)**: (1) fresh-env import gate test; (2) 220 + T0/T2 green; (3) listen_v2/
  query_my_cursors distinguish empty-success vs scan-failure (injection tests); (4) 9+ replay with
  missing/mixed start_time no crash; (5) L2 DEGRADED + spawn-fresh fallback, no PASS claim; (6) parity 32 +
  artifacts 33 + authoritative install entry + 20 tools + version identity = v0.4.0; (7) corrected
  completion report (AR dispositions + raw gate output) in the final commit.
- **Result**: PASS — all 6 AR + 3 N dispositions DONE, delivery commit + tag v0.4.0 prepared.
- **Confidence**: high — every AR locked by new tests except AR-04/06 (contract corrections; re-verified by
  report/plan text consistency) and AR-05's tag (commit-level identity). T46 re-test remains the standing
  upgrade path.

### T51 — Re-acceptance round 2: RAR-01~04 (customer REVISE_REQUESTED -> final gates ready)

- **Context**: customer's second-round review
  (`docs/superpowers/reviews/2026-08-03-hardening-reacceptance-review.md`) returned `REVISE_REQUESTED`
  (narrow scope, no Wave 1-4 reopen). Three real gaps + one docs gap, all confirmed against the code
  before fixing: (1) RAR-01 - `query_my_cursors` put `degraded_stores` INSIDE the cursor map, which
  `validate_cursors` (mcp_server listen_v2 entry) rejects as INVALID_ARGUMENT when the result is passed
  per docs; (2) RAR-02 - re-observing a PID does not refresh dict order, so `1..8 -> 1 -> 9` trims the
  just-re-observed 1 (false-dead -> needless resume/spawn); (3) RAR-03 - `.claude-plugin/plugin.json` +
  marketplace.json still `0.3.0`/"16 MCP tools" (actual 20 tools, tag v0.4.0) and `claude plugin list`
  reported that stale identity; README had no clean-checkout install path; (4) RAR-04 - report header
  facts stale (febc803/204/integration classification) + premature pass claims.
- **RAR-01 (P0)**: `query_my_cursors` -> stable wrapper `data = {cursors: {...}, degraded_stores: [...]}`
  (both paths same shape; `[]` when clean). Cursor map never carries metadata; SKILL + tool docstring say
  pass `data.cursors`. Rewrote the 2 shape-pinned tests + new entry-level composition test
  (degraded result -> `validate_cursors` passes -> `listen_v2` ok + degradation observable).
- **RAR-02 (P1)**: `kernel.py _handle_start` pop-then-reinsert on re-observed PID (recency refresh);
  start_time still validation-only. 2 tests: `1..8 -> 1(re-observed) -> 9` keeps 1 (order
  `[3..8,1,9]` pinned), only-1-alive `check_alive == 1`; same sequence through the persisted replay
  (`process_session_ctrl_event`).
- **RAR-03 (P1)**: plugin.json + marketplace.json (win AND wsl twins) -> `0.4.0` / "Exposes 20 MCP
  tools"; README new "Install / load" section (claude plugin marketplace add <v2_win> -> install ->
  list/details verify); new anti-drift gate `tests/unit/test_plugin_manifest_gate.py` (version == 0.4.0,
  tool count == real @mcp.tool count == 20, marketplace source -> canonical tree, win/wsl marketplace
  twin byte-identical - the twin is OUTSIDE build_artifacts' mirror scope). **Smoke**: `claude plugin
  validate` PASS + `claude plugin details cc-communicate@cc-communicate-local` now reports
  `cc-communicate 0.4.0 ... Exposes 20 MCP tools` (was 0.3.0/16).
- **RAR-04 (P2)**: completion report header (终点 a8927a0 + tag v0.4.0, tests 227, classification
  unit+parity, external review incl. 2 acceptance rounds) + §8 wording (final ACCEPTED pending) + §9.2
  RAR disposition table; response doc (§4 gate-6 claim corrected - "20 与 .mcp.json 一致" was wrong;
  .mcp.json doesn't enumerate tools, plugin.json is the manifest) + §8 RAR section; reacceptance review
  + response enter the delivery commit.
- **Auto gate (raw)**: `py -3 tools/run_regression.py` ->
  T0 syntax PASS (44 .py + 2 .js) / T1 pytest PASS (**227 passed** = 220 + 7 new) /
  T2 parity PASS (32) / T2 artifacts PASS (33, templates pinned) / **GATE: PASS**.
  Artifacts regenerated (manifests + server + SKILL + README + kernel); 0-diff holds.
- **Round-3 gates (审核方 §5, 6/6)**: (1) degraded query_my_cursors -> listen_v2 composes (no
  INVALID_ARGUMENT, degradation observable); (2) `1..8 -> 1 -> 9` order + check_alive + replay; (3)
  manifest 0.4.0/20 + parity/artifacts green; (4) README install path smoked via `claude plugin
  details`; (5) report facts + response + review doc in the delivery commit; (6) 220 + 7 new green,
  T0/T1/T2 all PASS.
- **Result**: PASS — RAR-01~04 dispositions DONE; tag v0.4.0 moved to the round-3 delivery commit
  (manifest now matches the tag); ready for final ACCEPTED.
- **Confidence**: high — every RAR locked by a regression test; the manifest fix verified through the
  real plugin CLI (not just the test).

### T52 — Re-acceptance round 3: FR-01/02 (release packaging, code ACCEPTED)

- **Context**: third-round review
  (`docs/superpowers/reviews/2026-08-03-hardening-reacceptance-round3-review.md`) verdict:
  **Runtime code `ACCEPTED`; installable release packaging `REVISE_REQUESTED_RELEASE_ONLY`** — two
  release items only, no runtime/protocol changes required.
- **FR-01 (P0, release gate)**: (a) README's clean-checkout steps lacked the server runtime deps
  install — `.mcp.json` launches system `python` directly, so a clean env would fail to start the MCP
  server; (b) the T51 smoke (`claude plugin validate`/`details`) proved metadata resolvable, NOT that
  the plugin loads. Fixes: README now has a step-0 "install into the SAME interpreter `.mcp.json`
  resolves" (verified in this env: CC resolves `python` -> `AppData\Local\Python\bin\python.exe`
  (pythoncore 3.14); git-bash's `python` hits the Microsoft Store stub — the README's verification
  command + stub note cover exactly that) + the verification command. **Real load smoke** (3 workers):
  script-import coordinator + real spawned CCs (bypass) — each called `my_session_id` through its MCP
  server and confirmed "cc-communicate MCP server CONNECTED and fully functional".
  - Worker 1b4283f7 (initial spawn; coordinator crashed before connect — rerun with same token
    reused the worker, no second window):
    `spawn_collaborator -> ok (WorkerHandle, permission_mode=bypass)`; `check_alive = 1`.
  - Worker 4a113f12 (fr01-tok-0001): `connect -> ok (correlation-matched reply)`; send ok
    (message_id a1da4416539b4b1b9dcae6c799b5a746); worker report:
    `"FR-01 report: my_session_id returned sid=4a113f12-9309-4806-b058-3a0692ccda21 (exact, from the
    first tool call). cc-communicate MCP server: CONNECTED and fully functional in this session -
    my_session_id, claim_pending_spawn, listen, and send_message all succeeded with ok=true."`
    final check_alive = 1; close_connection ok.
  - Worker 09571d6b (fr01-tok-0002, FINAL state after registry update to 0.4.1): MCP server process
    pid 1624 cmdline = `python -u .../v2_win/cc-communicate/server/mcp_server.py` ->
    **LOAD SOURCE = v2_win canonical** (NOT the cache snapshot; the directory-source marketplace
    resolves the plugin root to the canonical tree even after `claude plugin update`); worker report:
    `"session_id=09571d6b-4569-455b-aab3-5b3ee9367b5b. cc-communicate MCP server CONNECTED and fully
    functional: my_session_id OK, claim_pending_spawn claimed=True, listen OK (delivered hello + this
    request), send_message OK (hello-ack sent)."` PROBE: PASS.
- **FR-02 (P1, immutable identity)**: the moved v0.4.0 tag violates build-identity immutability.
  Fix: v0.4.0 left at `421a25e` forever; NEW immutable release **v0.4.1**: plugin.json +
  marketplace.json (win + wsl twins) -> `0.4.1`; drift gate `RELEASE_VERSION = "0.4.1"`;
  `claude plugin update cc-communicate@cc-communicate-local` refreshed the registry ->
  `claude plugin list` and `claude plugin details` both report `0.4.1 / Exposes 20 MCP tools`;
  new annotated tag v0.4.1 created once on the final commit.
- **Report cleanup**: §4.1/§9.2 enumeration fixed (manifest gate 3 + marketplace twin 1 = 7, the
  "gate 4 + twin 1" double-count inflated the literal sum); round-2 response (reviewer's cited
  filename `2026-08-03-reacceptance-round2-response.md`) + round-3 review + smoke evidence committed;
  report stays ACCEPTED-pending until the reviewer signs off.
- **Auto gate**: T0 syntax PASS (44 .py + 2 .js) / T1 pytest PASS (227 passed) /
  T2 parity PASS (32) / T2 artifacts PASS (33, templates pinned) / **GATE: PASS** (version gate
  updated to 0.4.1 and green).
- **Result**: PASS — FR-01/02 DONE; v0.4.1 tagged on the final commit; 5 final gates ready.
- **Confidence**: high — the load smoke is a real CC session calling through its actual MCP server
  (not metadata); the load-source probe captured the worker's MCP server process cmdline directly.

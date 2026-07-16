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
| connect reply matching (C3) | high (unit) | T17 - ts-filter rejects stale close-notice + self-connect hello; unit-tested, pending live |
| blocking listen + keep-listen law (C2) | high (unit) | T18 - listen is now a blocking tool (no bash/strays/exit-1); unit-tested, pending live (gate: 30s blocking MCP call) |
| listen.py UTF-8 / decode hardening (C5) | high (unit) | T19 - stdout UTF-8 + UnicodeDecodeError skip; matches exit-1-bg/exit-2-fg pattern; pending live |
| non-blocking terminate (C1) | high (logic) | T20 - close_connection fire-and-forget remote, always succeeds; pending live |
| conv registration persistence (R2) | high (unit) | T21 - alive_conversations.json survives restart; unit-tested, pending live restart |
| handshake guide + tool (C4) | medium | T22 - guide + help_connect_machines in both trees; cross-realm exec proven by Amd8; pending live orchestration |

> **Note (post-v0.2.0 robustness pass):** C1/C2/C3/C5/R2 are **implemented +
> unit-tested + parity-verified**, but NOT yet live-verified with real CCs. The
> two real-scene failures (background listen exit-1; collaborator stopped
> listening + went off-script to a bash loop) drove C2/C5. Live re-runs of the
> multi-round conversation + a kernel-restart-during-conv test are the next gate.

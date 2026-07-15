# V2 Implementation Log (2026-07-15)

Raw record of build + test actions. See `tested&2betest.md` for the summarized
test matrix and `handoff.md` for status.

## Build steps
1. Scaffold: `cp -r cc-communicate-marketplace v2_win`; drop v1 README/
   TEST_CHECKLIST; drop `listen_poller.py` (Amd3). Create `plans/`, `log/`.
2. Wrote/modified v2_win server files per spec (proc.py, proc.js, paths.py,
   machine_identity.py, spawn.py, check_core.py, kernel_api.py, kernel.py,
   listen.py, rpc_client.py, wake_kernel.py, user_functions.py, mcp_server.py,
   machine_sign_up.py, machine_add.py) + SKILL.md + bumped manifests to 0.2.0.
3. `cp -r v2_win v2_wsl`; `sed` `.mcp.json` `python` -> `python3`.
4. Re-synced v2_wsl from v2_win after each post-copy fix (docstring escape,
   kernel_terminate flag-file).

## Test outputs (key lines)

### Syntax (ast.parse, warnings->errors)
```
syntax: OK
```

### T2 imports + BUG-1 (Windows)
```
imports OK
detect_type: win-host
resolve_claude(test pid=9868): found claude ancestor pid=9600 start=1784079383.83
to_peer_perspective(win-host DATA_DIR -> wsl-ubuntu): /mnt/c/研究生/实习/learn AI/projects/cc-communicate/v2_win/cc-communicate/data
claude_binary_path: C:\Users\Mocry\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe
```

### T4 kernel lazy-start + RPC (Windows)
```
1) query_session (triggers kernel lazy-start)...
   query_session(nonexistent) = None
2) machine_identity: {'type': 'win-host', 'id': 'c3534710-...', 'claude_bin': None}
3) check_alive(nonexistent) = 0
4) terminating kernel... terminate sent
kernel.log: kernel starting (pid=..., machine=win-host) / kernel READY - 0 sessions
kernel.stderr.log: (empty)
```
(First run: kernel_terminate no-op -> force-killed pid 38664. Fixed -> T7.)

### T5 listen.py local (Windows)
```
wrote pipe file: <ts>__aaaa...__bbbb....md
[{"time": <ts>, "from_id": "aaaa...", "message": "hello reply from a"}]
listen exit=0
log dir after: <ts>__aaaa...__bbbb....md   (archived pipe->log)
```

### T6 WSL imports + detect_type (v2_wsl under wsl python3)
```
imports OK (all server modules)
detect_type: wsl-ubuntu
DATA_DIR: /mnt/c/研究生/实习/learn AI/projects/cc-communicate/v2_wsl/cc-communicate/data
to_peer_perspective(wsl->host): //wsl.localhost/Ubuntu/mnt/c/研究生/.../data
claude_binary_path: None   (expected: no WSL claude ancestor in test context)
```
Note: needed `MSYS_NO_PATHCONV=1` on the `wsl.exe` call (C2 confirmed).

### T7 kernel_terminate (flag-file fix)
```
query_session: None / terminate sent
core_status: {"status": 0, ...}
kernel.log: kernel exiting - writing status=0 / kernel exited
lingering? (none) = clean exit
```

### T8 parity
```
diff -rq v2_win v2_wsl: Files v2_win/cc-communicate/.mcp.json and v2_wsl/cc-communicate/.mcp.json differ
(only .mcp.json)
```

### T9 WSL deps
```
deps OK (psutil, filelock, mcp)
os.name: posix / is_wsl: True / WSL_DISTRO_NAME: Ubuntu
```

## Bugs found + fixed during build/test
1. `import json` placed at bottom of user_functions.py -> moved to top.
2. `C:\ ` invalid escape in machine_sign_up.py docstring -> `C:/`.
3. **kernel_terminate no-op** (kernel runs as `__main__`; `import kernel`
   touched a different module) -> flag-file mechanism (TERMINATE_FLAG).
4. MSYS path mangling on direct `wsl.exe` CLI calls (C2) -> confirmed; code uses
   subprocess list form to avoid; CLI tests use `MSYS_NO_PATHCONV=1`.

## Not done (deferred to §2 of tested&2betest.md)
- #1 hook WSL verification, #6 trust-flag, BUG-1 e2e, connect e2e, cross-realm
  e2e + remote-wake, 9p visibility, handshake round-trip. All need real CC
  spawning and/or WSL deployment.

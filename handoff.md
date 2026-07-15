# V2 Implementation Handoff (2026-07-15)

> **Purpose**: let a compacted session recover the full situation fast. Read this
> first, then `tested&2betest.md`, then the spec
> (`docs/superpowers/specs/2026-07-15-cc-communicate-v2.2-amendments-design.md`).

## 1. Status in one paragraph

V2 of cc-communicate is **implemented** as two complete, installable plugins
(`v2_win/`, `v2_wsl/`) covering `wsl2_core_plan.md` Phase 1+2 plus all v2.2
amendments. The shared code is platform-conditional; the two folders differ only
in `.mcp.json` (`python` vs `python3`). Core fixes are **verified locally**
(BUG-1 resolve_claude, BUG-5 dynamic paths, kernel lazy-start+RPC+terminate,
listen.py local archive, WSL module imports + type detection). What remains is
**end-to-end testing that needs real CC spawning + WSL deployment** -- the
make-or-break is whether `claude --resume` fires SessionStart (#1), and whether
`--dangerously-skip-permissions` actually skips the trust dialog (#6).

## 2. What V2 is + locked decisions

V2 = Windows host ↔ WSL2 cross-realm p2p. Two kernels (one per machine), each
**pure-local**; cross-machine fan-out is in the MCP server (user-space) via
`rpc_client.call_remote`. Cross-machine messages live on the HOST. Machine
registration is a one-time C:\ filesystem handshake.

Decisions (from brainstorming):
- **D1 / BUG-4**: `call_remote` wakes a dead remote kernel (runs its
  `wake_kernel.py` via cross-machine exec) + retries. Reuses the remote's
  filelock mutex (single-instance preserved).
- **D2 / #6**: spawned CCs launch with `--dangerously-skip-permissions`.
- **D3 / #3+#5**: `connect` polls for the reply **in-process** (no listener
  subprocess); a new `listen` tool returns the `listen.py` command (CC never
  builds the path).

## 3. Directory layout (cwd `cc-communicate/`)

```
v2_win/   win marketplace root (.claude-plugin/marketplace.json + cc-communicate/)
v2_wsl/   wsl marketplace root (identical except .mcp.json -> python3)
handoff.md            <- THIS FILE
tested&2betest.md     <- test record (done + to-do)
plans/2026-07-15-v2-implementation-plan.md
log/implementation-log.md
docs/superpowers/specs/2026-07-15-cc-communicate-v2.2-amendments-design.md  (committed)
cc-communicate-marketplace/   <- v1, kept as reference
core_plan.md, wsl2_core_plan.md, SESSION_HANDOFF.md, ToCollaboratorCC.md  <- v1 docs
```
`v2_wsl/` is the SOURCE to deploy into WSL's ext4 (copy to e.g.
`/home/<user>/v2_wsl/` inside WSL -- NOT run from /mnt/c, for ext4 performance).

## 4. What's implemented (v2 plugin files)

Shared (both `v2_win/cc-communicate/` and `v2_wsl/cc-communicate/`):
- `scripts/registrar.js`, `scripts/lib/paths.js` -- unchanged from v1.
- `scripts/lib/proc.js` -- **Amd1**: `isClaudeCmd` matches the cmdline's FIRST
  token (the executable), not a substring. Fixes the hook side of BUG-1.
- `server/proc.py` -- **Amd1**: `resolve_claude` matches by process NAME
  (`claude`/`claude.exe`), drops the cmdline skip-list. + `claude_binary_path()`.
- `server/paths.py` -- +`MACHINE_INFO_LOG_DIR`, `MACHINE_IDENTITY_FILE`,
  `TERMINATE_FLAG`.
- `server/machine_identity.py` -- NEW: `detect_type`, `load_or_create`,
  `to_peer_perspective` (dynamic drive/distro), `build_self_entry`, `wsl_distro_name`.
- `server/spawn.py` -- **Amd9** `--dangerously-skip-permissions`; v2.1 §2.3 Linux
  tmux branch; `_claude_bin()` uses identity's full path on WSL (C13).
- `server/check_core.py` -- v2.1 §2.4 Linux `start_new_session`.
- `server/kernel.py` -- machine_identity on init; `machine` field on sessions;
  dispatches new funcs (spawn_cc_new/resume, create_conversation_folder,
  kernel_terminate); arm_poller dispatch removed; `_should_exit` checks
  `TERMINATE_FLAG`; finally cleans the flag.
- `server/kernel_api.py` -- arm_poller REMOVED; collect_messages KEPT (kernel
  fn, used by close_connection drain + peer listen.py archive delegation); +
  spawn_cc_new/resume, create_conversation_folder, kernel_terminate (flag-file).
- `server/listen.py` -- NEW (Amd3): any-undelivered + direction-specific +
  settle 3s + fixed 2s poll; scans local + peer conversations/ (read-only);
  archives local direct, remote via `call_remote("collect_messages")`.
- `server/rpc_client.py` -- `call()` (local, unchanged) + `call_remote()`
  (Amd8: write remote queue, poll, wake-on-timeout + retry) + `_wake_remote()`.
- `server/wake_kernel.py` -- NEW (Amd8): `ensure_core()` entrypoint for remote wake.
- `server/user_functions.py` -- **Amd2** connect in-process poll; **Amd4**
  hello/prompts; **Amd6** hold_time=300; Phase2 routing (query_session/
  check_alive/query_conversations/send_message/evoke/close_connection fan out);
  `listen_command`, `query_machines`, `create_collaborator(machine=)`.
- `server/mcp_server.py` -- `listen` tool added; arm_poller/collect_messages
  removed; `query_machines` added; routing tools delegate to user_functions.
- `server/machine_sign_up.py` (WSL) / `server/machine_add.py` (host) -- NEW
  (Amd7): 4-way C:\ handshake; write machine_info_log entries with data_dir +
  data_dir_native + wake_interpreter + wake_script_native + distro.
- `skills/cc-communicate/SKILL.md` -- rewritten (listen tool, hold_time=300,
  connect-before-listen, cross-realm notes).
- `.claude-plugin/plugin.json` + marketplace.json -- bumped to 0.2.0.

## 5. What's verified (see tested&2betest.md for detail)

- All modules import cleanly on Windows AND WSL python3.
- BUG-1: `resolve_claude` finds the claude.exe ancestor by name (pid 9600).
- BUG-5: `to_peer_perspective` derives drive C -> `/mnt/c/` (win->wsl) and
  `//wsl.localhost/Ubuntu/` (wsl->host, distro from WSL_DISTRO_NAME).
- machine_identity: `detect_type` = `win-host` (Windows) / `wsl-ubuntu` (WSL).
- Kernel lazy-start + queue RPC: `query_session`/`check_alive` return correct
  None/0; machine_identity.json generated on init; clean exit via kernel_terminate.
- listen.py local path: detects a pipe message, reads, archives pipe->log,
  prints JSON, exits 0.
- WSL deps present (psutil, filelock, mcp).
- v2_win vs v2_wsl differ ONLY in .mcp.json.

## 6. Bugs found during implementation + their fix

- **kernel_terminate was a no-op** (BROKEN): the kernel runs as `__main__`, so
  `import kernel; kernel._exit_requested=True` touched a DIFFERENT module object.
  **Fixed** with a `TERMINATE_FLAG` file the kernel loop polls (paths.py +
  kernel.py + kernel_api.py). Re-tested: clean exit, status=0, no linger.
- **MSYS path mangling (C2) confirmed real**: calling `wsl.exe -- python3
  /mnt/c/...` from Git-Bash mangled the path. The code avoids it: `_wake_remote`
  uses subprocess LIST form (not a bash string), so no MSYS conversion. (Only
  direct CLI invocations need `MSYS_NO_PATHCONV=1`.)

## 7. What's NOT verified (needs user / WSL deployment)

These need real CC spawning and/or a deployed WSL side -- I cannot do them alone
without risking stray CC processes / trust prompts. See tested&2betest.md §2:
1. **#1 hook on WSL** (manual / .py-tmux / `--resume` SessionStart) -- the
   make-or-break: does `claude --resume` fire SessionStart?
2. **#6 trust dialog** -- does `--dangerously-skip-permissions` actually skip it?
3. **BUG-1 end-to-end** -- a spawned CC (prompt contains "cc-communicate") can
   `my_session_id` successfully (was failing pre-fix).
4. **connect end-to-end** -- real reply poll (two CCs).
5. **cross-realm e2e** -- deploy v2_wsl into WSL ext4, register machines, WSL↔host
   connect/send/listen/close; + remote-wake (kill host kernel, WSL connect).
6. **9p dir-change visibility** -- host writes, WSL `/mnt/c/` listdir latency
   (could delay listen.py cross-realm detection).

## 8. Key technical notes

- `data/` is gitignored (plugin .gitignore); runtime only.
- Conversation store: same-machine -> that machine; cross-machine -> HOST. A WSL
  caller reaching a host target registers/sends/polls on the HOST (via /mnt/c/
  read + call_remote archive).
- `call_remote` first window 10s then wake+retry (live kernel responds <1s; wake
  is mutex-safe so a spurious wake is harmless).
- `connect` reads the reply BEFORE archiving (no false-timeout if a stray
  listener races). SKILL.md disciplines "connect before listen".
- `claude_binary_path` returns None in a non-CC context (e.g. my WSL test via
  wsl.exe) -- expected; a real WSL CC has a WSL claude ancestor.

## 9. Next steps (post-compact)

1. User deploys `v2_wsl/` into WSL ext4 (e.g. `/home/<user>/v2_wsl/`), installs
   plugin in a WSL CC.
2. Run #1 hook verification (3 scenarios) -- resolve the `--resume` SessionStart
   question first; it determines whether evoke/connect-to-dead works at all.
3. Run #6 trust-flag verification (spawn a CC with the flag, confirm no dialog).
4. If both pass: full Phase-1 e2e (two WSL CCs), then Phase-2 e2e (WSL↔host,
   incl. remote-wake).
5. Fix anything that surfaces; update tested&2betest.md.

# V2 Implementation Plan (2026-07-15)

## Goal
Two complete, installable v2 cc-communicate plugins — `v2_win/` (Windows host)
and `v2_wsl/` (WSL2) — implementing `wsl2_core_plan.md` Phase 1+2 plus the
`2026-07-15-cc-communicate-v2.2-amendments-design.md` fixes.

## Deliverable layout (in cwd `cc-communicate/`)
```
v2_win/   <- win marketplace root (.claude-plugin/marketplace.json + cc-communicate/)
v2_wsl/   <- wsl marketplace root (same; .mcp.json uses python3)
handoff.md
tested&2betest.md
plans/    <- this file
log/      <- implementation/test logs
```
v1 (`cc-communicate-marketplace/`) kept as reference.

## Approach
Win and WSL share **one platform-conditional codebase**; only `.mcp.json` differs
(`python` vs `python3`). Build the full plugin in `v2_win/`, copy to `v2_wsl/`,
flip `.mcp.json`.

## Files (v2 plugin)

**Unchanged from v1 (copied):** `hooks/hooks.json`, `scripts/registrar.js`,
`scripts/lib/paths.js`, `server/conversations.py`, `server/requirements.txt`,
`.gitignore`.

**Modified:**
- `scripts/lib/proc.js` — Amd1: `isClaudeCmd` name-based (drop cmdline substring skip)
- `server/proc.py` — Amd1: `resolve_claude` name-based
- `server/paths.py` — +`MACHINE_INFO_LOG_DIR`, `MACHINE_IDENTITY_FILE`
- `server/spawn.py` — Amd9 `--dangerously-skip-permissions` + v2.1§2.3 Linux tmux branch
- `server/check_core.py` — v2.1§2.4 Linux `start_new_session`
- `server/kernel.py` — machine_identity gen on init, `machine` field, dispatch new kernel funcs
- `server/kernel_api.py` — Amd3 remove `arm_poller`/`collect_messages`; +`create_conversation_folder`, `spawn_cc_new`/`spawn_cc_resume`, `query_machines`, `kernel_terminate`; `machine` field
- `server/rpc_client.py` — Amd8 `call_remote` + wake-on-timeout
- `server/user_functions.py` — Amd2 connect in-process; Amd4 hello/prompts; Amd6 hold_time=300; Phase2 cross-realm routing
- `server/mcp_server.py` — Amd3 `listen` tool; remove `arm_poller`/`collect_messages`; Phase2 tools (`query_machines`) + routing
- `skills/cc-communicate/SKILL.md` — Amd-SKILL rewrite
- `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` — bump v0.2.0

**New:**
- `server/listen.py` — Amd3 merged listener (any-undelivered, direction-specific, settle 3s, fixed 2–3s)
- `server/wake_kernel.py` — Amd8 remote-wake entrypoint (`ensure_core()`)
- `server/machine_sign_up.py` — Amd7 WSL-side handshake
- `server/machine_add.py` — Amd7 host-side handshake
- `server/machine_identity.py` — type detection + identity file helper

## Phasing
1. Scaffold (copy v1 → v2_win, drop `listen_poller.py`). ✅
2. Write modified + new files into `v2_win/cc-communicate/`.
3. `cp -r v2_win v2_wsl`; flip `v2_wsl/.../.mcp.json` → `python3`.
4. Test (see `tested&2betest.md`).
5. Write `handoff.md`, `tested&2betest.md`, logs.

## Test scope (preview)
**Can test myself:** Python syntax/imports; `resolve_claude` fix (BUG-1) via psutil on live claude procs; machine_identity type detection; handshake path-transform logic; `listen.py`/`call_remote` logic by code review + dry runs.
**Cannot fully test (need user/WSL deployment):** real CC spawn end-to-end (trust flag + interaction); cross-realm e2e (deploy v2_wsl into WSL ext4 + 2 CCs); `--resume` SessionStart (the make-or-break).

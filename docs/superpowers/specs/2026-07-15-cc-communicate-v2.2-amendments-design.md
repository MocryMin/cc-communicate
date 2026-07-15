# cc-communicate v2.2 Amendments — Design

> **Document position**: Amendment layer over `wsl2_core_plan.md` (v2.1). Read
> order: `core_plan.md` → `wsl2_core_plan.md` → this file. This doc captures the
> bug fixes and design decisions resolved during the 2026-07-15 brainstorming
> session, before v2 implementation begins. All items here are **deltas vs
> v2.1**; anything not mentioned is unchanged from v2.1.
>
> **Status**: design approved 2026-07-15. Next step: writing-plans →
> implementation plan.

---

## 0. Decisions locked

| # | Topic | Decision | Rationale |
|---|---|---|---|
| D1 | BUG-4 remote kernel wake | **A**: `call_remote` wakes the remote kernel on timeout (cross-machine exec of the remote's `ensure_core`) + one retry | Reuses the remote's existing `filelock` mutex → single-instance preserved for free; WSL→host wake channel verified feasible |
| D2 | #6 trust prompt on spawned CC | **B**: spawned sub-CCs launch with `--dangerously-skip-permissions` | Design intent is delegating automation to CC; broad permission bypass accepted for spawned agent CCs |
| D3 | #3 double-poller + #5 plugin_root | **A**: `connect` polls for the reply **in-process** (no listener subprocess); a new `listen` tool returns the `listen.py` command for CC's async listening | Eliminates the two-listener-process starvation at the root; CC never needs to know `<plugin_root>` |

---

## 1. Bugs found (root causes)

| ID | Bug | Root cause | Severity | Fixed by |
|---|---|---|---|---|
| BUG-1 | `my_session_id` fails in every spawned/evoked CC | `proc.py::resolve_claude` skips any ancestor whose **cmdline** contains `"cc-communicate"`; the spawn/evoke prompts literally contain it, so the claude binary parent is rejected → returns `(None,None)`. (`proc.js` has a fallback that accidentally saves the hook side; `proc.py` does not.) Root cause confirmed: claude keeps CLI args in argv (observed pid 19588 `claude.exe -r`). | Critical — breaks create_collaborator + evoke reply | Amd 1 |
| BUG-2 | `arm_poller` command hardcodes `python` | `kernel_api.py:199` `f'python "..."'`; WSL has no `python` (C12). | High for Phase 1 | Amd 5 |
| BUG-3 | `hold_time` default 60 vs plan 300 | `mcp_server.py:102`, `user_functions.py:30`, SKILL.md still 60; only docs bumped to 300. | Low | Amd 6 |
| BUG-4 | Cross-machine RPC fails when remote kernel idle-exited | `call_remote` does not ensure_core the remote (v2.1 §3.5.6); if no remote CC is active, the remote kernel self-exited → request never processed → 30s timeout. | High for Phase 2 | Amd 8 (D1) |
| BUG-5 | Handshake path transform hardcoded to drive `C:` + distro `Ubuntu` | `wsl2_core_plan.md` #W11 examples hardcode `C:\↔/mnt/c/` and `Ubuntu`; breaks for other drives / distros. | Medium (portability) | Amd 7 |
| #1 | Hook never verified on WSL | No test of SessionStart/End landing in `session_ctrl/` for WSL CCs. Make-or-break sub-question: does `claude --resume` fire SessionStart? | Verification task | §3 V2 |
| #2 | Portability / DB location | Code layer is already portable (`__file__`-relative); only the handshake transforms hardcode locations. Data under `PLUGIN_ROOT/data` is acceptable. | Medium | Amd 7, Amd 11 |
| #3 | Double poller | Caller pre-arms a bg poller AND `connect` spawns an internal poller — both watch `toid==caller`; first to archive starves the other. | High | Amd 2 (D3) |
| #4 | `connect` hello lacks reply instruction | `hello = "connect hello from " + sid`; recipient isn't told to reply. | Medium | Amd 4 |
| #5 | After removing `arm_poller`, CC can't discover `<plugin_root>` | v0.1 `arm_poller` returned the full command; v2 removes it but expects CC to assemble the path with no discovery interface. | High | Amd 3 (D3) |
| #6 | WSL2 spawned CC blocked by workspace-trust prompt | tmux-spawned CC in a new cwd shows the trust dialog; prompt never reaches it. | High (blocks #1) | Amd 9 (D2) |

---

## 2. Amendments (deltas vs v2.1)

### Amd 1 — `resolve_claude` matches the binary, not the cmdline (BUG-1)

**Files**: `server/proc.py::resolve_claude`; `scripts/lib/proc.js::isClaudeCmd` (+ `resolveClaude`).

**Change**: identify the claude ancestor by **process name** (`claude` / `claude.exe`), not by a substring of the full cmdline. Drop the `if "cc-communicate" in cmdline: continue` skip-list in `proc.py`, and the `!/cc-communicate/i.test(cmd)` exclusion in `proc.js::isClaudeCmd`.

**Why safe**: our own scripts run under `python`/`node` (name `python`/`node`); intermediate shells are `cmd`/`bash`/`tmux`. None have name `claude`. So a name check alone distinguishes the real claude binary from processes whose cmdline merely *references* claude (e.g. `cmd /c start claude --resume <sid> "<prompt>"`). The cmdline skip-list was matching the **prompt text**, which is why spawned CCs lost their ancestor.

**proc.js note**: the hook side currently survives via a `ppid` fallback; make it principled (direct name match) instead of relying on the fallback.

**Verify**: root cause already confirmed (claude keeps args in argv — pid 19588). End-to-end: after Amd 9 unblocks spawning, spawn a CC with a cc-communicate prompt and confirm `my_session_id` returns its sid (§3 V3).

### Amd 2 — `connect` polls in-process, no listener subprocess (D3, #3)

**Files**: `server/user_functions.py::connect`.

**Change**: replace steps 6–7 (arm_poller + `subprocess.run(listen_poller)` + collect_messages) with an **in-process poll**:
```
after send hello:
  deadline = now + hold_time
  while now < deadline:
    reply = scan conversations/pipe for files where toid == caller_sid
            (Phase 2 WSL: also scan /mnt/c/.../conversations/ read-only)
    if reply file found:
      content = read immediately            # read BEFORE any archive
      best-effort os.replace(pipe -> log)   # claim; ignore FileNotFoundError
      return "connect succeed; reply: " + content
    sleep ~0.5s
  withdraw(init_connect) ; return "connect failed, timeout"
```

**Why this kills the double-poller**: `connect` is no longer a listener *process*. The only listener process that ever exists is the one CC runs via the `listen` tool (Amd 3). The old failure mode (two listener processes starving each other on `count_undelivered`) cannot occur.

**Residual (pre-arm listener)**: if CC violates "connect before listen" and runs a bg `listen.py` first, both may detect the reply. `connect` reads content **immediately on detect** (before `listen.py`'s 3s settle), so `connect` always obtains the content → **no false timeout**. `listen.py` may also deliver → **benign duplicate**. SKILL.md disciplines "connect before listen" to avoid the duplicate. No per-sid lock is needed (read-before-claim makes `connect` robust).

**Cross-realm archive**: archiving a cross-machine reply (in host `conversations/`) is delegated to the host kernel via `call_remote("collect_messages", …)` — WSL is read-only on host conversations (v2.1 #W7).

### Amd 3 — `listen` tool + `listen.py` (D3, #5)

**Files**: new `server/listen.py`; `server/mcp_server.py` (add `listen`, remove `arm_poller` + `collect_messages`); `server/kernel_api.py` (remove `arm_poller`, `collect_messages`); delete `server/listen_poller.py`.

**`listen` MCP tool**: `listen(session_id, timeout=300) -> dict` returns
`{"command": "<sys.executable> '<PLUGIN_ROOT>/server/listen.py' <sid> <timeout>"}`.
CC runs `command` via `Bash(run_in_background=true)`. This replaces `arm_poller`'s "command" role — CC never constructs the path itself.

**`listen.py`** (per v2.1 §3.4.4, unchanged): any-undelivered + direction-specific (`toid == sid`) + settle 3s + fixed 2–3s interval; reads `data/server/machine_identity.json` for type routing (WSL: local + `/mnt/c/` read-only; host: local only). Archives `pipe -> log` (local direct; cross-machine delegates host kernel). Prints messages JSON to stdout, exit 0; exit 2 on timeout.

**Breaking change**: `arm_poller` and `collect_messages` removed; SKILL.md rewritten (§Amd-SKILL).

### Amd 4 — hello + evoke/create_collaborator prompts (#4)

**Files**: `server/user_functions.py` (hello, create_collaborator prompt); `server/kernel_api.py::evoke` (default prompt).

- **hello** (`user_functions.py:72`): `"connect hello from <caller_sid>. This is a p2p connection request — reply immediately with any message to establish the channel."`
- **evoke default prompt** (`kernel_api.py:176`): instruct the revived CC to discover itself and listen: `"You have been revived for p2p communication by cc-communicate. Call my_session_id to learn your id, then call listen and run the returned command in the background, and reply to any hello from peer sessions."`
- **create_collaborator prompt** (`user_functions.py:146`): same shape — `my_session_id` → `listen` (bg) → reply to hello.

(Necessary-but-not-sufficient with Amd 1: the recipient must be listening. The prompts now make spawned/revived CCs listen.)

### Amd 5 — `arm_poller` command uses `sys.executable` (BUG-2)

**File**: `server/kernel_api.py:199`. `cmd = f'"{sys.executable}" "{PLUGIN_ROOT}/server/listen_poller.py" "{session_id}"'` (add `import sys`). Moot once Amd 3 removes `arm_poller`, but required for Phase 1 reuse and as a safety fix.

### Amd 6 — `hold_time` default 300 (BUG-3)

**Files**: `server/user_functions.py:30,135`; `server/mcp_server.py:102,131`; `skills/cc-communicate/SKILL.md`. All `hold_time` defaults → 300.

### Amd 7 — Handshake path transform is dynamic (BUG-5, #2)

**Files**: new `server/machine_sign_up.py` (WSL), new `server/machine_add.py` (host).

Each side writes the **peer's-perspective** data_dir (v2.1 #W11), computed dynamically:
- **Host → WSL perspective**: transform own `C:\…\data` → `/mnt/<drive>/…/data`, deriving `<drive>` from the path's leading letter (not hardcoded `c`).
- **WSL → host perspective**: transform own `/home/…/data` → `//wsl.localhost/<distro>/…/data`, using `$WSL_DISTRO_NAME` (verified available, =`Ubuntu` here) — not hardcoded.

**Deferred edge**: custom `/mnt` prefix via `wsl.conf automount` — read `/etc/wsl.conf` later if needed; default `/mnt` covers standard WSL.

### Amd 8 — Remote kernel wake (D1, BUG-4)

**Files**: new `server/wake_kernel.py` (or `check_core.py` `__main__`); `server/rpc_client.py` (add `call_remote`).

**`wake_kernel.py`**: calls `ensure_core()`, exits. Single entrypoint reused by both local and remote wake.

**`call_remote(machine, function, args, timeout=30)`** (v2.1 §3.5.6 + wake-on-timeout):
1. Write request to remote `data/queue/` (path from `machine["data_dir"]`).
2. Poll remote `data/queue/responses/` — **first window ~10s** (a live kernel responds in <1s).
3. On timeout: **wake the remote kernel** via cross-machine exec, then re-submit + poll once more (~30s):
   - WSL→host: `subprocess.run(["python.exe", "<host>/server/wake_kernel.py"])` — list form avoids MSYS path conversion (C2). **Verified feasible**: `python.exe` callable from WSL, Windows python 3.14 has `psutil`+`filelock`.
   - host→WSL: `subprocess.run(["wsl.exe","-d",distro,"--","python3","<wsl>/server/wake_kernel.py"], errors="replace")` — per #W2 / C2–C4.
4. Still no response → return null/0/failed (no further retry).

**Mutex**: the wake runs the remote's `ensure_core`, which acquires the remote's `filelock` on `core_status.json.lock`. Concurrent wakes (from either side) serialize on it → **single-instance preserved, no new mutex**. `call_remote` does **not** ensure_core the local kernel.

### Amd 9 — Spawned CCs get `--dangerously-skip-permissions` (D2, #6)

**File**: `server/spawn.py::spawn_cc_new`, `spawn_cc_resume` (both the Windows `cmd /c start` and WSL `tmux` branches). Add `"--dangerously-skip-permissions"` to the claude argv so spawned CCs skip the workspace-trust dialog. Accepted per D2 (spawned CCs are automation agents).

### Amd 10 — Hook WSL verification (#1)

Verification task, not a code change. See §3 V2.

### Amd 11 — Portability / DB location (#2)

Keep `data` under `PLUGIN_ROOT/data` (already portable via `__file__`-relative in `paths.py`/`paths.js`). The only portability fix needed is Amd 7 (dynamic handshake paths). Moving `data` to a user-home dir is **deferred** (future item, not blocking).

### Amd-SKILL — SKILL.md rewrite

Update for: `listen` tool (replaces arm_poller/collect_messages 3-step), `hold_time=300`, "connect before listen" discipline, cross-machine notes. Breaking change flagged.

---

## 3. Verification plan

| ID | Item | Method | Status |
|---|---|---|---|
| V1 | BUG-4 WSL→host wake feasibility | `python.exe` interop from WSL + Windows python deps | ✅ Done (2026-07-15): python.exe callable, psutil+filelock present |
| V2 | #1 Hook on WSL (3 scenarios) | After Amd 9 unblocks spawn: (a) manually-started CC, (b) `.py` tmux spawn, (c) `claude --resume <sid>`. For each, confirm `data/session_ctrl/` gets start (with correct pid) and end events. **Critical**: does `--resume` fire SessionStart? Borrow the running pid 19588 `claude -r`. | Pending |
| V3 | BUG-1 end-to-end | After V2: spawn a CC with a cc-communicate prompt; confirm `my_session_id` returns its sid (failed pre-Amd-1). | Pending |
| V4 | 9p dir-change visibility | Host writes a file; WSL `os.listdir(/mnt/c/…)` — measure time-to-visible. If > settle/poll window, adjust `listen.py`. | Pending |
| V5 | Phase 1 e2e | Two WSL CCs: connect → send → listen → reply → close. | Pending |
| V6 | Phase 2 e2e + remote wake | WSL CC ↔ host CC: connect → send → listen → reply → close. Then kill host kernel, WSL CC connects → verify host kernel wakes (Amd 8). | Pending |

---

## 4. Out of scope / deferred

- User-home `data` dir (keep `PLUGIN_ROOT/data`).
- Custom `/mnt` prefix handling (default `/mnt`).
- macOS / pure-Linux (no WSL) support — v2 is WSL2-specific.
- `launch.py` auto-dependency-install wrapper (roadmap).

---

*Last updated: 2026-07-15. Amendments to `wsl2_core_plan.md` v2.1 from the 2026-07-15 brainstorming session. All technical claims either code-derived or empirically verified (see §3).*

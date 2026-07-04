# cc-communicate — manual test checklist

Run this yourself (manual testing). Check each box; if a result deviates from
Expected, that's a bug — stop and report it before continuing.

## Part A — Functional tests (dev machine)

Run on the dev machine (Python deps already installed). Repeat Phase 0 hygiene
cleanup before every full run.

### Phase 0 — Hygiene cleanup

Run in **PowerShell** from the `cc-communicate-marketplace` directory.

- [ ] Kill leftover kernels:
      `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*kernel.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }`
      Expected: no error (or no output if none found).
- [ ] Clean runtime state:
      `Remove-Item -Force cc-communicate\data\server\core_status.json* -ErrorAction SilentlyContinue; Remove-Item -Recurse -Force cc-communicate\data\queue, cc-communicate\data\conversations -ErrorAction SilentlyContinue`
      Expected: no error.

### Phase 1 — Install

- [ ] `/plugin marketplace add "<absolute path to cc-communicate-marketplace>"`
      Expected: marketplace added confirmation.
- [ ] `/plugin install cc-communicate@cc-communicate-local`
      Expected: install success.
- [ ] Fully restart CC (close and reopen the session).
      Expected: SessionStart hook fires on the new session.

### Phase 2 — Install verify

- [ ] `/mcp` — Expected: cc-communicate server listed.
- [ ] `my_session_id()` — Expected: a UUID string (not `"failed, ..."`).
- [ ] `query_session(<your sid>)` — Expected: a dict with pid, cwd, started_at, etc.

### Phase 3 — Two-CC setup

- [ ] Open a second CC (CC-B) in another terminal; restart it after install so
      the plugin is active.
      Expected: two CCs running, both with the plugin.
- [ ] `my_session_id()` in CC-A — record as sid_A. Expected: UUID.
- [ ] `my_session_id()` in CC-B — record as sid_B. Expected: UUID, differs from sid_A.

### Phase 4 — Core p2p loop

- [ ] In CC-A: `connect(sid_A, sid_B)` — Expected: `"connect succeed; reply: ..."`.
      If `"failed, ..."` or `"connect failed, ..."`, stop — bug.
- [ ] In CC-A: `send_message(sid_A, sid_B, "hello from A")` — Expected: `"message_sent at <ts>"`.
- [ ] In CC-B: `arm_poller(sid_B, timeout=600)` — record the `command`.
      Expected: `{armed: True, command: "python .../listen_poller.py <sid_B>", timeout: 600, baseline: <n>}`.
- [ ] In CC-B: run the command via `Bash(run_in_background: true)`.
      Expected: a background task starts; CC-B is free.
- [ ] In CC-A: `send_message(sid_A, sid_B, "second message")`.
- [ ] In CC-B: wait for `<task-notification>` (poller exited 0).
      Expected: notification within a few seconds.
- [ ] In CC-B: `collect_messages(sid_B)` — Expected: `[{time, from_id: sid_A, message: "hello from A"}, {time, from_id: sid_A, message: "second message"}]` (sorted by time).
- [ ] In CC-B: reply — `send_message(sid_B, sid_A, "hi from B")` — Expected: `"message_sent at <ts>"`.
- [ ] In CC-A: `arm_poller(sid_A, timeout=600)` + `Bash(command, bg=true)`, wait
      for task-notification, `collect_messages(sid_A)` — Expected: sees `"hi from B"`.

### Phase 5 — Teardown

- [ ] In CC-A: `close_connection(sid_A, sid_B)` — Expected: `{closed: True, delivered_pending: [...]}`.
- [ ] In CC-B: `arm_poller(sid_B)` + `Bash(bg)`, wait, `collect_messages(sid_B)` —
      Expected: a message with `message: "[CONNECTION CLOSED by <sid_A>]"`.

### Phase 6 — Spawn collaborator

- [ ] In CC-A: `create_collaborator(sid_A, "C:/tmp/collab-test", hold_time=60)` —
      Expected: a new CC window opens in that cwd; within ~30s returns `"connect succeed; reply: ..."`.
      If `"failed, new session did not register within 30s (...)"`, the new CC didn't load the plugin (check user-level install).
- [ ] `query_conversations(sid_A)` — Expected: lists both sid_B and the new collaborator's sid.

### Phase 7 — Edge cases

- [ ] `check_alive(sid_B)` while CC-B is alive — Expected: `1`.
- [ ] Close CC-B, then `check_alive(sid_B)` — Expected: `0` (record dropped).
- [ ] `query_session("00000000-0000-0000-0000-000000000000")` — Expected: `null`.
- [ ] `evoke(<a dead sid>)` — Expected: `"evoke spawned (resumed)"`; then `check_alive(<that sid>)` after a few seconds — Expected: `1`.
- [ ] `query_conversations(sid_A)` — Expected: `[{partner: ...}, ...]`.
- [ ] `withdraw(sid_A, sid_B, init_connect=1)` — Expected: `"conversation withdrawn"`; the conversation folder is removed.

---

<!-- Part B (portability test) is appended by Task 5 after the root README install spec is updated. -->

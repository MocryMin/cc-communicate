# cc-communicate

A p2p transport for Claude Code sessions: message pipes, connection lifecycle
(connect/listen/close), structured envelopes, and spawn/revive of collaborator
sessions — same machine or Windows-host ↔ WSL2.

## Install / load (the single authoritative path, RAR-03)

This canonical tree IS the installable artifact (a directory-source
marketplace; `v2_wsl/` is the generated WSL twin). From a clean checkout:

```bash
# 1. register the canonical tree as a local marketplace (name from
#    .claude-plugin/marketplace.json = "cc-communicate-local")
claude plugin marketplace add "C:\path\to\cc-communicate\v2_win"
# 2. install the plugin
claude plugin install cc-communicate@cc-communicate-local
# 3. verify the loaded build identity + tool count
claude plugin list        # cc-communicate ... Version: 0.4.0 ... enabled
claude plugin details cc-communicate@cc-communicate-local   # Exposes 20 MCP tools
```

The plugin's `.mcp.json` launches `server/mcp_server.py` (20 FastMCP tools)
via `${CLAUDE_PLUGIN_ROOT}`; in any session with the plugin enabled,
`my_session_id` answering your sid is the load smoke test. WSL2 deployments
use the GENERATED `v2_wsl/` tree the same way (`claude plugin marketplace
add "C:\path\to\cc-communicate\v2_wsl"`). The repo-root
`cc-communicate-marketplace/` is **historical only — do not install from it**
(AR-05).

## Threat model

This plugin is built for a **trusted single-user, trusted registered peer
realm** and is **NOT safe against a malicious local process with data-dir
access**.

- The data root (`data/`) is plaintext JSON with no authentication: sessions,
  message pipes, connection state, and the operation journal are readable and
  writable by anything that can reach the files.
- Any local process that can write `data/` can impersonate a session, forge
  messages, or poison connection/spawn state — the plugin provides no
  cryptographic authentication.
- Cross-machine peers are trusted by registration (a one-time handshake), not
  by credentials.
- Full authentication is deliberately out of scope until the threat model
  widens; this plugin does not pretend to be authenticated.

What IS enforced: session/message/connection id charset and length (HP-06),
path containment for destructive operations, single-active connection per pair
(HP-05), per-store cursor ACK semantics (HP-02), and the permission_mode
spawn policy below.

## Spawn permission policy (permission_mode)

| mode | meaning |
|---|---|
| `standard` (DEFAULT for new spawns) | The spawned CC makes normal permission decisions — a workspace-trust dialog may appear, and coordinator-driven autonomy requires human approval. |
| `bypass` | Explicit opt-in for unattended automation. Splices `--dangerously-skip-permissions`; the spawned CC runs fully autonomous. The legacy `create_collaborator` wrapper and the resume path (evoke) are bypass. |

A spawned `standard`-mode CC may stall at the trust dialog until a human
approves — that is the designed cost of the secure default.

## Resume (evoke) status — DEGRADED (AR-04)

`evoke`/`spawn_cc_resume` restores the **process/session** (resume lands in
the original cwd, check_alive → 1), but on CC v2.1.220 the revived CC's
cc-communicate MCP client may come up disconnected, so **delivery after
resume is unreliable** (T46, 2/2 failed — CC-side quirk, no cc-communicate
error). When the channel must work, prefer **spawn-fresh**
(`spawn_collaborator`); re-test after a CC update to upgrade the status.

## Configuration (env vars)

`CC_COMMUNICATE_DATA_DIR`, `CC_COMMUNICATE_MAX_INLINE_BYTES`,
`CC_COMMUNICATE_MAX_ARTIFACT_REFS`, `CC_COMMUNICATE_MAX_BACKLOG`,
`CC_COMMUNICATE_PENDING_SPAWN_TTL_SECONDS`, `CC_MONITOR_IDLE_TIMEOUT` —
documented per-feature in SKILL.md.

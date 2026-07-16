# cc-communicate cross-machine handshake guide

Follow this step by step when the user wants to connect/link/register this
machine to a peer (Windows host <-> WSL). It is a one-time setup; afterwards
cross-realm `connect` / `send_message` / `evoke` work automatically.

You (this CC) can drive BOTH sides yourself via cross-realm exec - the same
mechanism the plugin uses to wake a peer kernel. Do not make the user run the
scripts by hand if you can run them yourself.

## Prerequisites (ask the user)
1. "Is the cc-communicate plugin installed on the other machine?" Must be yes.
2. "What is the plugin install path on the other machine?" You need the exact
   directory that contains `server/machine_add.py` (host) or
   `server/machine_sign_up.py` (WSL). On WSL this is a Linux path like
   `/home/<user>/projects/v2_wsl/cc-communicate`; on Windows a path like
   `C:\...\v2_win\cc-communicate`.

## Step 1 - Identify this side
- Windows host: your shell is Windows; paths look like `C:\...`.
- WSL: your shell is Linux; `/mnt/c/...` is the host's C: drive.

## Step 2 - Run the handshake (drive both sides)
The handshake needs `machine_add.py` listening on the HOST and
`machine_sign_up.py` run on WSL. They rendezvous at `C:\` (= `/mnt/c` from WSL).

### If you are on the Windows host
1. Start the host listener in the background:
   `python "<host_path>\server\machine_add.py"`  (run in background)
2. Run the WSL signup (cross-realm exec). Replace `Ubuntu` and `<wsl_path>`:
   `wsl.exe -d Ubuntu -- bash -c 'python3 "<wsl_path>/server/machine_sign_up.py"'`
   IMPORTANT: keep the WSL path INSIDE the `bash -c '...'` single quotes. Git-bash
   mangles a `/mnt/...` or `/home/...` path passed directly to `wsl.exe` (it gets
   rewritten to a `C:\Program Files\Git\...` prefix). Inside `bash -c` the path
   is passed literally.
3. Wait for `machine_sign_up.py` to print `success!`.

### If you are on WSL
1. Start the host listener (cross-realm exec), in the background:
   `python.exe "<host_path>\server\machine_add.py"`  (run in background)
2. Run the WSL signup locally:
   `python3 "<wsl_path>/server/machine_sign_up.py"`
3. Wait for `success!`.

## Step 3 - Verify
Call `query_machines()`. It must return a non-empty dict with the peer's entry
(its `type`, `data_dir`, `distro`, wake fields). If empty, the handshake failed.

## Step 4 - Diagnose failures
- `machine_sign_up.py` says `no echo from host within 60s`: `machine_add.py` is
  not running on the host, or `C:\` is not writable from WSL.
- `query_machines()` empty despite `success!`: the `machine_info_log/` write
  failed - check the plugin `data/` dir is writable.
- Path errors from `wsl.exe ...`: git-bash mangled the path - re-run with the
  path inside `bash -c '...'`.
- `machine_add.py` says `timeout: no WSL registered within 5 min`: the WSL side
  didn't sign up in time; restart `machine_add.py` and re-run signup.

## Notes
- Idempotent: re-running overwrites the same `<id>.json` entries (machine ids are
  persistent). Safe to repeat.
- `machine_add.py` serves one registration then exits. To register a second
  distro, run it again.
- After success the peer is reachable until its `machine_info_log/` entry is
  deleted. There is no heartbeat; a dead peer's entry stays, and cross-realm
  calls to it time out (10s) then attempt a wake, then return None.

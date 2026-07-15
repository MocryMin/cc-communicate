"""Tool-side RPC client: call kernel functions via the queue folder.

Local calls use call() (ensure_core + local queue). Cross-machine calls use
call_remote() (v2.2 Amd8): write to the remote queue, poll the remote
responses, and on timeout WAKE the remote kernel (run its wake_kernel.py via
cross-machine exec) then retry once. call_remote never touches the LOCAL
kernel - the remote's lifecycle is managed by wake/remote CCs.

Request file:  <queue>/<ts>_<request_id>.json
Response file: <queue>/responses/<request_id>.json

Remote request_ids are prefixed with the local machine type (C14) to avoid
collisions between two machines generating uuid4s in the same remote queue.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import uuid

from check_core import ensure_core
from paths import QUEUE_DIR, QUEUE_RESPONSES_DIR, ensure_runtime_dirs

_DEFAULT_TIMEOUT = 30.0
_REMOTE_FIRST_WINDOW = 10.0   # live kernel responds in <1s; dead -> wake after this
_POLL_INTERVAL = 0.05


class KernelError(Exception):
    """Raised when the local kernel returns an error or can't respond in time."""


# ---------- local kernel ----------

def _submit(function: str, args: dict) -> str:
    rid = uuid.uuid4().hex
    req = {"request_id": rid, "function": function, "args": args}
    name = f"{int(time.time() * 1000):013d}_{rid}.json"
    tmp = os.path.join(QUEUE_DIR, name + ".tmp")
    final = os.path.join(QUEUE_DIR, name)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(req, f)
    os.replace(tmp, final)
    return rid


def _consume_response(rid: str):
    path = os.path.join(QUEUE_RESPONSES_DIR, rid + ".json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            resp = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    try:
        os.remove(path)
    except OSError:
        pass
    return resp


def call(function: str, args: dict | None = None, timeout: float = _DEFAULT_TIMEOUT):
    """Call a LOCAL kernel function. Raises KernelError on error/timeout (one
    retry, core_plan #11c)."""
    if args is None:
        args = {}
    ensure_runtime_dirs()
    last_rid = None
    for attempt in (1, 2):
        if not ensure_core():
            if attempt == 2:
                raise KernelError("kernel not alive; could not start it")
            time.sleep(_POLL_INTERVAL)
            continue
        rid = _submit(function, args)
        last_rid = rid
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            resp = _consume_response(rid)
            if resp is not None:
                if resp.get("error"):
                    raise KernelError(resp["error"])
                return resp.get("result")
            time.sleep(_POLL_INTERVAL)
    raise KernelError(
        f"timeout waiting for kernel response to {function} (rid={last_rid})")


# ---------- remote kernel (cross-machine) ----------

def _submit_remote(rqueue: str, function: str, args: dict) -> str:
    from machine_identity import local_type
    prefix = local_type().replace("-", "_")
    rid = f"{prefix}_{uuid.uuid4().hex}"
    req = {"request_id": rid, "function": function, "args": args}
    name = f"{int(time.time() * 1000):013d}_{rid}.json"
    os.makedirs(rqueue, exist_ok=True)
    tmp = os.path.join(rqueue, name + ".tmp")
    final = os.path.join(rqueue, name)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(req, f)
    os.replace(tmp, final)
    return rid


def _consume_remote(rresp: str, rid: str):
    path = os.path.join(rresp, rid + ".json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            resp = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    try:
        os.remove(path)
    except OSError:
        pass
    return resp


def _wake_remote(machine: dict):
    """Run the remote's wake_kernel.py via cross-machine exec so its ensure_core
    (filelock mutex) starts the remote kernel. Best-effort; mutex makes it safe
    even if the kernel was already alive. Reuses the remote's single-instance
    lock - no new mutex (v2.2 Amd8 / D1)."""
    mtype = machine.get("type", "")
    interp = machine.get("wake_interpreter")
    script = machine.get("wake_script_native")
    if not interp or not script:
        return
    try:
        if mtype == "win-host":
            # We are WSL, peer is host: Windows python via interop (list form
            # avoids MSYS path conversion - C2).
            subprocess.run([interp, script], capture_output=True,
                           timeout=20, errors="replace")
        else:
            # We are host, peer is WSL: wsl.exe (C2-C4 cautions handled by list
            # form + errors=replace).
            distro = machine.get("distro") or "Ubuntu"
            subprocess.run(["wsl.exe", "-d", distro, "--", interp, script],
                           capture_output=True, timeout=20, errors="replace")
    except Exception:
        pass


def call_remote(machine: dict, function: str, args: dict | None = None,
                timeout: float = _DEFAULT_TIMEOUT):
    """Call a function on a REMOTE kernel. On first-window timeout, wake the
    remote kernel and retry once. Returns the result, or None on failure (never
    raises - callers treat None as 'peer unreachable'). Does NOT ensure_core the
    local kernel."""
    if args is None:
        args = {}
    ensure_runtime_dirs()
    rqueue = os.path.join(machine["data_dir"], "queue")
    rresp = os.path.join(rqueue, "responses")
    for attempt in (1, 2):
        rid = _submit_remote(rqueue, function, args)
        window = _REMOTE_FIRST_WINDOW if attempt == 1 else timeout
        deadline = time.monotonic() + window
        while time.monotonic() < deadline:
            resp = _consume_remote(rresp, rid)
            if resp is not None:
                if resp.get("error"):
                    return None
                return resp.get("result")
            time.sleep(_POLL_INTERVAL)
        if attempt == 1:
            _wake_remote(machine)  # kernel likely dead -> wake + retry
    return None

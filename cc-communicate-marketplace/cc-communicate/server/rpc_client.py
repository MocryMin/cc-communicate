"""Tool-side RPC client: call kernel functions via the queue folder.

Every user-function MCP tool uses call() to invoke a kernel function:
  1. ensure_core() — make sure the kernel is alive (check_core).
  2. Write a request file to data/queue/.
  3. Poll data/queue/responses/<request_id>.json.
  4. On timeout, re-run ensure_core + re-submit once (core_plan #11c: the kernel
     may have died after our check, so the request was never processed).

The kernel side (kernel.py drain_queue) reads the request, dispatches it, writes
the response, and removes the request file.

Request file:  data/queue/<ts>_<request_id>.json   (ts-prefixed for lex order)
Response file: data/queue/responses/<request_id>.json
"""
from __future__ import annotations

import json
import os
import time
import uuid

from check_core import ensure_core
from paths import QUEUE_DIR, QUEUE_RESPONSES_DIR, ensure_runtime_dirs

_DEFAULT_TIMEOUT = 30.0
_POLL_INTERVAL = 0.05


class KernelError(Exception):
    """Raised when the kernel returns an error or can't respond in time."""


def _submit(function: str, args: dict) -> str:
    """Write the request file. Returns the request_id."""
    rid = uuid.uuid4().hex
    req = {"request_id": rid, "function": function, "args": args}
    # Prefix with ms timestamp so the kernel processes oldest-first (lex sort).
    name = f"{int(time.time() * 1000):013d}_{rid}.json"
    tmp = os.path.join(QUEUE_DIR, name + ".tmp")
    final = os.path.join(QUEUE_DIR, name)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(req, f)
    os.replace(tmp, final)
    return rid


def _consume_response(rid: str):
    """Read and delete the response file. Returns the parsed response or None."""
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
    """Call a kernel function and return its result.

    Raises KernelError on kernel error, or if no response within `timeout`
    after one retry (core_plan #11c exit-race mitigation)."""
    if args is None:
        args = {}
    ensure_runtime_dirs()

    last_rid = None
    for attempt in (1, 2):  # one retry after timeout
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
        # Timed out — kernel may have died. Loop: ensure_core detects+restarts.
    raise KernelError(
        f"timeout waiting for kernel response to {function} (rid={last_rid})")

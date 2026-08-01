"""Structured result/error codes + response envelope (D7 / HP-07).

The envelope is the MCP API contract: every tool returns
{ok, code, message, data, retryable} built by ok()/err(). code is None on
success; retryable=True only for transient failures where the caller should
retry the same operation. The kernel does NOT use envelopes - it returns raw
structured dicts and user_functions wraps them.
"""
from __future__ import annotations


class Code:
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NOT_FOUND = "NOT_FOUND"
    PEER_UNREACHABLE = "PEER_UNREACHABLE"
    TIMEOUT = "TIMEOUT"
    CONFLICT = "CONFLICT"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    NOT_ALIVE = "NOT_ALIVE"
    INTERNAL = "INTERNAL"


def ok(data=None) -> dict:
    return {"ok": True, "code": None, "message": None,
            "data": data, "retryable": False}


def err(code: str, message: str, data=None, retryable: bool = False) -> dict:
    return {"ok": False, "code": code, "message": message,
            "data": data, "retryable": retryable}

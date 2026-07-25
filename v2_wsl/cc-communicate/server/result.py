"""Structured result/error codes (D7; minimal Wave 1 form).

Wave 1 lands the code enum + minimal ok/err constructors so HP-06 validation
failures carry a stable, machine-checkable code. The full response envelope
(arrives in Wave 2 / HP-07) will wrap every tool result; until then tools keep
returning legacy strings/dicts, and validation failures surface as error
strings prefixed with the code ("INVALID_ARGUMENT: ...") so callers can branch
on the code prefix WITHOUT parsing natural language.
"""
from __future__ import annotations


class Code:
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NOT_FOUND = "NOT_FOUND"
    PEER_UNREACHABLE = "PEER_UNREACHABLE"
    TIMEOUT = "TIMEOUT"
    CONFLICT = "CONFLICT"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    INTERNAL = "INTERNAL"


def ok(data=None) -> dict:
    return {"ok": True, "code": None, "data": data}


def err(code: str, message: str, data=None) -> dict:
    return {"ok": False, "code": code, "message": message, "data": data}

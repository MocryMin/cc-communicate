"""Single validation layer for all external input (HP-06).

Rules:
  - Failure RAISES InvalidArgumentError (code INVALID_ARGUMENT) - we NEVER
    silently sanitize into another valid id (two invalid ids could then map to
    the same path).
  - Invoked at BOTH trust boundaries: MCP tool entry (mcp_server) and kernel
    dispatch (kernel._dispatch - which also covers remote RPC requests, since
    a peer's call_remote lands in this same queue).
  - conversations.conv_dir/pipe_filename enforce session-id validation as the
    deepest defense: no path is ever constructed from an unvalidated id.

Session ids: real CC session ids are UUIDs; synthetic/test ids are restricted
slugs. Both fit ^[A-Za-z0-9-]{1,128}$ - no underscores (so SEP '__' can never
appear inside an id), no slashes, no dots, no control chars. cwd gets NO
character whitelist (real cwds contain CJK chars and spaces - this repo's own
path does) - only absolute + existing-directory checks.
"""
from __future__ import annotations

import os
import re

from result import Code

MAX_ID_LEN = 128
# At least one alphanumeric (so "-" / "---" are rejected), then only
# [A-Za-z0-9-] up to the cap - no underscores, slashes, dots, control chars.
_ID_RE = re.compile(r"^(?=.*[A-Za-z0-9])[A-Za-z0-9-]{1,128}$")
# Inline payload cap (D5 value, enforced at send entry; HP-09 owns the rest of
# the resource policy in Wave 3).
MAX_INLINE_BYTES = int(os.environ.get("CC_COMMUNICATE_MAX_INLINE_BYTES",
                                      str(1024 * 1024)))


class InvalidArgumentError(ValueError):
    """Raised by every validator. drain_queue serializes type+message into the
    RPC error channel, so clients see 'InvalidArgumentError: INVALID_ARGUMENT: ...'."""
    code = Code.INVALID_ARGUMENT

    def __init__(self, message: str):
        super().__init__(f"{self.code}: {message}")


def _check_id(value, kind: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise InvalidArgumentError(
            f"{kind} must be 1-{MAX_ID_LEN} chars of [A-Za-z0-9-] with at "
            f"least one alphanumeric (no underscores, slashes, dots or "
            f"control chars); got {value!r}")
    return value


def validate_session_id(value) -> str:
    return _check_id(value, "session_id")


def validate_message_id(value) -> str:
    return _check_id(value, "message_id")


def validate_operation_id(value) -> str:
    return _check_id(value, "operation_id")


def validate_store_id(value) -> str:
    return _check_id(value, "store_id")


def validate_connection_id(value) -> str:
    """connection_id: uuid4 hex or any id-charset token (same rule as
    message_id - it doubles as a correlation key)."""
    return _check_id(value, "connection_id")


def validate_spawn_token(value) -> str:
    """spawn_token: uuid4 hex or any id-charset token (HP-04)."""
    return _check_id(value, "spawn_token")


def validate_message_size(message) -> str:
    if not isinstance(message, str):
        raise InvalidArgumentError(
            f"message must be a str; got {type(message).__name__}")
    n = len(message.encode("utf-8"))
    if n > MAX_INLINE_BYTES:
        raise InvalidArgumentError(
            f"message is {n} bytes, over the {MAX_INLINE_BYTES}-byte inline cap "
            f"(CC_COMMUNICATE_MAX_INLINE_BYTES); use artifact_refs instead")
    return message


def validate_cwd(value) -> str:
    """Absolute + existing directory. NO character whitelist (CJK/space ok)."""
    if not isinstance(value, str) or not value:
        raise InvalidArgumentError(f"cwd must be a non-empty str; got {value!r}")
    if not os.path.isabs(value):
        raise InvalidArgumentError(f"cwd must be absolute; got {value!r}")
    if not os.path.isdir(value):
        raise InvalidArgumentError(
            f"cwd is not an existing directory; got {value!r}")
    return value


def validate_cursors(value) -> dict:
    """Per-store cursor map {store_id: sequence:int>=0} (consumed by HP-02)."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InvalidArgumentError(
            f"cursors must be a dict of store_id->sequence; "
            f"got {type(value).__name__}")
    out = {}
    for k, v in value.items():
        validate_store_id(k)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise InvalidArgumentError(
                f"cursor for store {k!r} must be an int >= 0; got {v!r}")
        out[k] = v
    return out


def validate_bool(value) -> bool:
    if not isinstance(value, bool):
        raise InvalidArgumentError(
            f"expected a bool; got {type(value).__name__}")
    return value


def resolve_under(root: str, *parts: str) -> str:
    """Join + realpath; the result MUST stay strictly under root."""
    base = os.path.realpath(root)
    target = os.path.realpath(os.path.join(base, *parts))
    if target == base or not target.startswith(base + os.sep):
        raise InvalidArgumentError(
            f"resolved path {target!r} escapes its allowed root {base!r}")
    return target


def validate_spawn_entry(caller_sid: str, cwd: str, machine: dict):
    """HP-06 entry validation for create_collaborator. caller_sid is ALWAYS
    validated (it is a local id on every machine). cwd is validated ONLY for
    local spawns (machine is None): a peer-perspective cwd (machine given) is
    not absolute/existing on THIS machine, and the peer kernel re-validates it
    with its own filesystem semantics at dispatch (kernel _ARG_VALIDATORS
    spawn_cc_new) - defense in depth is preserved. Returns an INVALID_ARGUMENT
    error string, or None when all checks pass."""
    try:
        validate_session_id(caller_sid)
        if machine is None:
            validate_cwd(cwd)
    except InvalidArgumentError as e:
        return str(e)
    return None

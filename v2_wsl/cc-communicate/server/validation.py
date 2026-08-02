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
# Max artifact_refs per message (D5; bounds the worst-case record size).
MAX_ARTIFACT_REFS = int(os.environ.get("CC_COMMUNICATE_MAX_ARTIFACT_REFS", "16"))


class InvalidArgumentError(ValueError):
    """Raised by every validator. drain_queue serializes type+message into the
    RPC error channel, so clients see 'InvalidArgumentError: INVALID_ARGUMENT: ...'."""
    code = Code.INVALID_ARGUMENT

    def __init__(self, message: str):
        super().__init__(f"{self.code}: {message}")


class ResourceExhaustedError(InvalidArgumentError):
    """Over a resource budget (D5): maps to code RESOURCE_EXHAUSTED at the
    entry boundary; carries structured bytes data for the caller."""
    code = Code.RESOURCE_EXHAUSTED

    def __init__(self, message: str, data: dict = None):
        super().__init__(message)
        self.data = data


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
        raise ResourceExhaustedError(
            f"message is {n} bytes, over the {MAX_INLINE_BYTES}-byte inline cap "
            f"(CC_COMMUNICATE_MAX_INLINE_BYTES); use artifact_refs instead",
            data={"limit_bytes": MAX_INLINE_BYTES, "actual_bytes": n})
    return message


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def validate_artifact_refs(value) -> list:
    """artifact_refs (D5): [{path|uri (EXACTLY one), size int>=0, sha256
    64-hex, media_type non-empty str}], at most MAX_ARTIFACT_REFS entries.
    None -> []. Any violation raises InvalidArgumentError (schema error, not
    resource pressure). Returns canonical 4-field dicts (unknown keys
    dropped)."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidArgumentError(
            f"artifact_refs must be a list; got {type(value).__name__}")
    if len(value) > MAX_ARTIFACT_REFS:
        raise InvalidArgumentError(
            f"artifact_refs has {len(value)} entries, over the "
            f"{MAX_ARTIFACT_REFS} cap (CC_COMMUNICATE_MAX_ARTIFACT_REFS)")
    out = []
    for i, ref in enumerate(value):
        if not isinstance(ref, dict):
            raise InvalidArgumentError(
                f"artifact_refs[{i}] must be a dict; got {type(ref).__name__}")
        loc = None
        for key in ("path", "uri"):
            if ref.get(key) is not None:
                loc = key
                break
        if loc is None:
            raise InvalidArgumentError(
                f"artifact_refs[{i}] needs exactly one of 'path'/'uri'")
        other = "uri" if loc == "path" else "path"
        if ref.get(other) is not None:
            raise InvalidArgumentError(
                f"artifact_refs[{i}] must have exactly one of 'path'/'uri' "
                f"(both present)")
        if not isinstance(ref[loc], str) or not ref[loc]:
            raise InvalidArgumentError(
                f"artifact_refs[{i}].{loc} must be a non-empty str")
        size = ref.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise InvalidArgumentError(
                f"artifact_refs[{i}].size must be an int >= 0; got {size!r}")
        sha = ref.get("sha256")
        if not isinstance(sha, str) or not _SHA256_RE.match(sha):
            raise InvalidArgumentError(
                f"artifact_refs[{i}].sha256 must be 64 lowercase hex chars")
        mt = ref.get("media_type")
        if not isinstance(mt, str) or not mt:
            raise InvalidArgumentError(
                f"artifact_refs[{i}].media_type must be a non-empty str")
        out.append({loc: ref[loc], "size": size, "sha256": sha,
                    "media_type": mt})
    return out


def validate_permission_mode(value) -> str:
    """HP-10 (D4): spawn permission mode - "standard" (default for NEW
    spawns; the spawned CC makes normal permission decisions) or "bypass"
    (explicit opt-in for unattended automation; skips the trust dialog)."""
    if value not in ("standard", "bypass"):
        raise InvalidArgumentError(
            f"permission_mode must be 'standard' or 'bypass'; got {value!r}")
    return value


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

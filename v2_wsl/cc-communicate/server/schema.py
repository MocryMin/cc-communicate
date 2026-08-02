"""Data-root schema conventions + migration helpers (HP-11, D2).

Single source of truth for the supported schema_version and the
check/stamp/wrap/layout logic shared by the kernel loaders and
tools/migrate_data.py. Deliberately does NOT import paths - it works off an
arbitrary data root.

Rules:
  - schema_too_new(): an int schema_version > SUPPORTED_SCHEMA means a NEWER
    plugin wrote the file - refuse to interpret it (skip + loud log). The
    file is NEVER touched by this wave's code.
  - Missing / non-int schema_version is TOLERATED: today's v1 files were
    written before the convention was universal.
  - wrap_v1(): the flat registries (sessions / alive_conversations /
    ack_timestamps) CANNOT be stamped in place - a schema_version key would
    be misread as a session/ack entry, and a bare list cannot carry one.
    They wrap to {schema_version: 1, <key>: <payload>}; loaders dual-read
    (HP-01 legacy-.md precedent).
  - stamp_v1(): add-key stamp for self-describing dict files
    (machine_identity - its loader ignores unknown keys).
"""
from __future__ import annotations

import json
import os

import fileutil

SUPPORTED_SCHEMA = 1

# Persistent state files checked for schema_version (relative to the data root).
STATE_FILES = (
    "server/sessions.json", "server/alive_conversations.json",
    "server/ack_timestamps.json", "server/message_sequence.json",
    "server/cursors.json", "server/operation_journal.json",
    "server/machine_identity.json", "server/gc_state.json",
)
# Flat registries that predate the universal stamp: wrap to the versioned
# shape (cannot be stamped in place - see module docstring).
WRAP_TARGETS = (("server/sessions.json", "sessions"),
                ("server/alive_conversations.json", "conversations"),
                ("server/ack_timestamps.json", "ack_timestamps"))
# Self-describing dict files: add-key stamp.
STAMP_TARGETS = ("server/machine_identity.json",)

REQUIRED_DIRS = ("server", "session_ctrl", "queue", "queue/responses",
                 "conversations", "pending_spawn", "machine_info_log")


def schema_too_new(data, supported: int = SUPPORTED_SCHEMA) -> bool:
    """True only for an int schema_version > supported (a newer plugin wrote
    the file). Missing / non-int / bool / <= supported -> False."""
    if not isinstance(data, dict):
        return False
    v = data.get("schema_version")
    return isinstance(v, int) and not isinstance(v, bool) and v > supported


def unwrap(data, key: str):
    """Dual-read helper (HP-01 precedent): a wrapped dict
    {schema_version, <key>: payload} -> payload; anything else passes
    through unchanged (legacy flat shapes). Wrapped-but-missing-key -> None."""
    if isinstance(data, dict) and "schema_version" in data:
        payload = data.get(key)
        return payload if isinstance(payload, (dict, list)) else None
    return data


def needs_stamp(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and "schema_version" not in data


def stamp_v1(path: str) -> bool:
    """Add schema_version: 1 to an unstamped dict file (atomic). Returns
    True when it stamped; False for absent/non-dict/already-stamped."""
    if not needs_stamp(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    data["schema_version"] = SUPPORTED_SCHEMA
    fileutil.atomic_write_json(path, data)
    return True


def needs_wrap(path: str, key: str) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    return not (isinstance(data, dict) and "schema_version" in data)


def wrap_v1(path: str, key: str) -> bool:
    """Wrap a flat v1 registry into {schema_version: 1, <key>: <payload>}
    (atomic). Returns True when it wrapped; False for absent/non-JSON or
    already-wrapped files."""
    if not needs_wrap(path, key):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    fileutil.atomic_write_json(
        path, {"schema_version": SUPPORTED_SCHEMA, key: data})
    return True


def validate_layout(root: str):
    """(errors, warnings): errors = state files with a NEWER schema_version
    (REFUSED - never touched); warnings = missing runtime dirs (advisory -
    the kernel creates them) + unreadable state files."""
    errors, warnings = [], []
    for rel in REQUIRED_DIRS:
        if not os.path.isdir(os.path.join(root, rel)):
            warnings.append(f"missing runtime dir: {rel} (kernel creates it)")
    for rel in STATE_FILES:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            warnings.append(f"unreadable state file: {rel}")
            continue
        if schema_too_new(data):
            errors.append(
                f"{rel}: schema_version {data.get('schema_version')} > "
                f"supported {SUPPORTED_SCHEMA} - REFUSED (untouched)")
    # connection info files + pending markers
    cdir = os.path.join(root, "conversations")
    if os.path.isdir(cdir):
        for name in os.listdir(cdir):
            info = os.path.join(cdir, name, "info.json")
            if os.path.isfile(info):
                try:
                    with open(info, encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    continue
                if schema_too_new(data):
                    errors.append(
                        f"conversations/{name}/info.json: schema_version "
                        f"{data.get('schema_version')} > supported "
                        f"{SUPPORTED_SCHEMA} - REFUSED (untouched)")
    pdir = os.path.join(root, "pending_spawn")
    if os.path.isdir(pdir):
        for name in os.listdir(pdir):
            if not name.endswith(".json"):
                continue
            path = os.path.join(pdir, name)
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if schema_too_new(data):
                errors.append(
                    f"pending_spawn/{name}: schema_version "
                    f"{data.get('schema_version')} > supported "
                    f"{SUPPORTED_SCHEMA} - REFUSED (untouched)")
    return errors, warnings

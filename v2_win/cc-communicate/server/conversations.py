"""Conversation folder/pipe path helpers (core_plan "conversations结构").

A conversation is a p2p channel between two sessions. The folder name is
canonical — the two session_ids sorted and joined by SEP — so connect(A,B) and
connect(B,A) resolve to the same folder (core_plan #8: order-independent).

Layout:
  data/conversations/<sid_a>__<sid_b>/      (sids sorted)
    info.json          conversation metadata (future)
    pipe/              undelivered messages: <ts>__<fromid>__<toid>.md
    log/               delivered/archived messages (same filename scheme)

SEP is '__' (double underscore). Session_ids are UUIDs (hyphens only, no
underscores), so '__' never appears inside a session_id — splitting on it is
unambiguous.

Pipe filenames put the zero-padded timestamp FIRST so lexical sort yields
chronological order (same convention as the lower layer's event files). This
matters for collect_messages (future) reading messages in order.
"""
from __future__ import annotations

import os

from paths import CONVERSATIONS_DIR
from validation import validate_session_id
import message_record

SEP = "__"


def conv_dir(sid_a: str, sid_b: str) -> str:
    """Canonical conversation directory for the pair (order-independent: the
    two sids are sorted before joining). Validates both ids (HP-06) - no path
    is ever built from an unvalidated id."""
    a, b = sorted([validate_session_id(sid_a), validate_session_id(sid_b)])
    return os.path.join(CONVERSATIONS_DIR, a + SEP + b)


def find_conv_dir(sid_a: str, sid_b: str):
    """Return the conversation dir path if it exists, else None. Order-independent
    (canonical). O(1) — creation is always canonical, so exact match suffices;
    core_plan #8's contains-check is satisfied by the sort."""
    d = conv_dir(sid_a, sid_b)
    return d if os.path.isdir(d) else None


def ensure_conv_dir(sid_a: str, sid_b: str) -> str:
    """Create the conversation folder (+ pipe/, log/) if absent. Returns its path."""
    d = conv_dir(sid_a, sid_b)
    os.makedirs(os.path.join(d, "pipe"), exist_ok=True)
    os.makedirs(os.path.join(d, "log"), exist_ok=True)
    return d


def pipe_filename(fromid: str, toid: str, ts: int) -> str:
    """Filename for a pipe message: <ts:013d>__<fromid>__<toid>.md.
    ts-first so lex sort = chronological. Validates ids (HP-06)."""
    validate_session_id(fromid)
    validate_session_id(toid)
    return f"{int(ts):013d}{SEP}{fromid}{SEP}{toid}.md"


def parse_pipe_filename(name: str):
    """Parse a pipe filename back to (ts, fromid, toid), or None if malformed.
    Strips a trailing .md if present."""
    if name.endswith(".md"):
        name = name[:-3]
    parts = name.split(SEP)
    if len(parts) != 3:
        return None
    ts_s, fromid, toid = parts
    try:
        return int(ts_s), fromid, toid
    except ValueError:
        return None


def count_undelivered(session_id: str) -> int:
    """Count undelivered pipe messages addressed to session_id, across ALL
    conversation folders. Used by arm_poller (baseline) and listen_poller
    (detection).

    Scanning all folders each call (rather than watching fixed dirs) means a
    folder appearing AFTER arming — e.g. a brand-new partner's first message —
    is still detected. Cheap for typical conversation counts."""
    total = 0
    try:
        entries = os.listdir(CONVERSATIONS_DIR)
    except FileNotFoundError:
        return 0
    for name in entries:
        parts = name.split(SEP)
        if len(parts) != 2 or session_id not in parts:
            continue
        pipe = os.path.join(CONVERSATIONS_DIR, name, "pipe")
        if not os.path.isdir(pipe):
            continue
        for fname in os.listdir(pipe):
            parsed = parse_pipe_filename(fname)
            if parsed and parsed[2] == session_id:  # toid == session_id
                total += 1
    return total


def info_path(sid_a: str, sid_b: str) -> str:
    """Path of the conversation's info.json (connection metadata, HP-05)."""
    return os.path.join(conv_dir(sid_a, sid_b), "info.json")


def parse_any_pipe_filename(name: str):
    """Normalize BOTH pipe filename formats (reader-side dispatcher, HP-01).

    Legacy v0.3: <ts:013d>__<from>__<to>.md
        -> {"format": "legacy", "ts": int, "from_id", "to_id",
            "sequence": None, "message_id": None}
    Record v1:   <seq:020d>__<from>__<to>__<message_id>.json
        -> {"format": "record", "ts": None, "from_id", "to_id",
            "sequence": int, "message_id": str}
    Malformed -> None. Tolerant by design: this is a reader, not a boundary.
    """
    if name.endswith(".md"):
        parsed = parse_pipe_filename(name)
        if not parsed:
            return None
        ts, fromid, toid = parsed
        return {"format": "legacy", "ts": ts, "from_id": fromid,
                "to_id": toid, "sequence": None, "message_id": None}
    parsed = message_record.parse_record_filename(name)
    if not parsed:
        return None
    seq, fromid, toid, mid = parsed
    return {"format": "record", "ts": None, "from_id": fromid,
            "to_id": toid, "sequence": seq, "message_id": mid}

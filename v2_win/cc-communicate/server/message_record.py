"""Versioned message record (envelope) + atomic publish (HP-01, D6).

Record schema v1:
  {schema_version, message_id, store_id, sequence, from_session, to_session,
   kind, correlation_id, causation_id, created_at_ms, payload: {text}}

  - sequence: per-store monotonic number allocated by the store's single
    kernel thread - the ONLY ordering/ACK unit (HP-02 builds cursors on it).
    Gaps are allowed (crash between counter persist and publish); a sequence
    is NEVER reused.
  - message_id: uuid4 hex - end-to-end identity and the dedup unit (HP-03).
    Embedded in the final filename, so even a sequence bug cannot overwrite an
    existing message.
  - created_at_ms: display/diagnostic ONLY - never a correctness field.

Filename: <sequence:020d>__<from>__<to>__<message_id>.json
  from/to are embedded so pipe scans filter WITHOUT opening every file (a
  deliberate deviation from the proposal's <seq>__<mid>.json; the envelope
  stays the source of truth - filename fields are a routing cache). Validated
  ids contain no '__' (HP-06), so the 4-field split is unambiguous.

Pure module: every path comes in as a parameter (test isolation).
"""
from __future__ import annotations

import json
import os
import time
import uuid

import fileutil

SCHEMA_VERSION = 1
RECORD_SUFFIX = ".json"


def new_record(store_id: str, sequence: int, from_session: str, to_session: str,
               text: str, kind: str = "text", correlation_id=None,
               causation_id=None, message_id: str = None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "message_id": message_id or uuid.uuid4().hex,
        "store_id": store_id,
        "sequence": int(sequence),
        "from_session": from_session,
        "to_session": to_session,
        "kind": kind,
        "correlation_id": correlation_id,
        "causation_id": causation_id,
        "created_at_ms": int(time.time() * 1000),
        "payload": {"text": text},
    }


def record_filename(record: dict) -> str:
    return "%020d__%s__%s__%s%s" % (
        record["sequence"], record["from_session"], record["to_session"],
        record["message_id"], RECORD_SUFFIX)


def parse_record_filename(name: str):
    """-> (sequence:int, from_session, to_session, message_id) or None."""
    if not name.endswith(RECORD_SUFFIX):
        return None
    parts = name[:-len(RECORD_SUFFIX)].split("__")
    if len(parts) != 4:
        return None
    seq_s, from_s, to_s, mid = parts
    try:
        seq = int(seq_s)
    except ValueError:
        return None
    if not mid:
        return None
    return seq, from_s, to_s, mid


def publish(conv_d: str, record: dict) -> str:
    """Atomically publish the record into <conv_d>/pipe/. Returns filename."""
    fname = record_filename(record)
    fileutil.atomic_write_json(os.path.join(conv_d, "pipe", fname), record)
    return fname


def read_record(path: str):
    """json load; None on ANY failure (missing/partial/malformed/non-dict)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None

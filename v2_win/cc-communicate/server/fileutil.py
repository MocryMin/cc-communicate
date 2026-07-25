"""Atomic file-write primitives with fsync (C1 / HP-01).

tmp file in the SAME directory -> flush -> fsync -> os.replace. Correctness
anchor across realms (NTFS / WSL ext4 / DrvFs /mnt/c / 9P //wsl.localhost):
the reader NEVER sees a partial file because the final path appears via atomic
rename. Crash-durability of the rename itself varies by filesystem (fsync on
DrvFs/9P is weak); recovery is anchored in the persistent sequence counter +
message_id dedup (master plan R2). Stale .tmp.<pid> residue is ignored by all
readers (suffix-based scans never match it).
"""
from __future__ import annotations

import json
import os


def atomic_write_bytes(path: str, data: bytes):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: str, obj, indent=None):
    atomic_write_bytes(path, json.dumps(obj, indent=indent).encode("utf-8"))
